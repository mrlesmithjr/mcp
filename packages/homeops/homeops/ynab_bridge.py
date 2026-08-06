"""YNAB integration - bridge homeops data to budget planning.

Generates recommendations for YNAB planned expenses based on:
- Appliance replacement forecasts (sinking fund targets)
- Upcoming maintenance costs (from task schedule)
- Utility budget recommendations (from trend data)

This module produces data/recommendations. Actual YNAB writes are done
by Claude via ynab-tools MCP, keeping homeops read-only toward YNAB.

Routed through homeops.log_store/log_compute (issue #58) so `budget_overview`
and `sinking_fund_plan` (the MCP tools) and `homeops budget *` (the CLI
commands, which share these same functions) reflect whichever
`home_log.backend` is configured, instead of always reading SQLite
directly. The actual sinking-fund/upcoming-maintenance/utility-budget math
lives in homeops.log_compute so it is identical to what the equivalent
report tools compute; these functions just fetch rows and shape the
composed dict each entry point returns.
"""

from datetime import date

from homeops import log_compute, log_store


def appliance_sinking_fund_plan(config):
    """Generate sinking fund recommendations based on appliance replacement timelines.

    For each appliance with a known replacement cost and remaining lifespan,
    calculates the monthly savings needed to fund replacement by end of life.

    Returns list of dicts: {name, replacement_cost, remaining_years,
    monthly_savings, annual_savings, urgency}
    """
    appliances = log_compute.list_appliances(log_store.read_table(config, "appliances"))
    return log_compute.appliance_sinking_fund_plan(appliances)


def upcoming_maintenance_costs(config):
    """Estimate upcoming maintenance costs from task schedule.

    Looks at all active tasks and their typical costs from history
    to forecast near-term spending.

    Returns list of upcoming costs with estimated amounts.
    """
    tasks = log_compute.list_tasks(log_store.read_table(config, "tasks"))
    return log_compute.upcoming_maintenance_costs(tasks)


def utility_budget_recommendation(config):
    """Generate utility budget recommendations based on historical averages.

    Compares current year spending to historical averages and suggests
    monthly budget targets.

    Returns dict with per-type recommendations.
    """
    summary = log_compute.get_utility_summary(log_store.read_table(config, "utility_bills"))
    return log_compute.utility_budget_recommendation(summary)


def budget_overview(config):
    """Comprehensive budget planning overview combining all data sources.

    Returns a unified view for Claude to use when discussing budget
    with the user.
    """
    sinking = appliance_sinking_fund_plan(config)
    maintenance = upcoming_maintenance_costs(config)
    utilities = utility_budget_recommendation(config)
    costs = log_compute.get_cost_summary(log_store.read_table(config, "costs"), str(date.today().year))

    ytd_total = sum(c["total"] or 0 for c in costs) if costs else 0

    return {
        "appliance_sinking_funds": sinking,
        "upcoming_maintenance": maintenance,
        "utility_recommendations": utilities,
        "ytd_spending": {
            "total": round(ytd_total, 2),
            "by_category": costs,
        },
    }
