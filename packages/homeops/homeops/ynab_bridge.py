"""YNAB integration - bridge homeops data to budget planning.

Generates recommendations for YNAB planned expenses based on:
- Appliance replacement forecasts (sinking fund targets)
- Upcoming maintenance costs (from task schedule)
- Utility budget recommendations (from trend data)

This module produces data/recommendations. Actual YNAB writes are done
by Claude via ynab-tools MCP, keeping homeops read-only toward YNAB.
"""

from datetime import date

from homeops.db.appliances import list_appliances
from homeops.db.costs import get_cost_summary
from homeops.db.tasks import list_tasks
from homeops.db.utilities import get_utility_summary


def appliance_sinking_fund_plan(config):
    """Generate sinking fund recommendations based on appliance replacement timelines.

    For each appliance with a known replacement cost and remaining lifespan,
    calculates the monthly savings needed to fund replacement by end of life.

    Returns list of dicts: {name, replacement_cost, remaining_years,
    monthly_savings, annual_savings, urgency}
    """
    appliances = list_appliances(config)
    plans = []

    for a in appliances:
        cost = a.get("replacement_cost")
        remaining = a.get("remaining_lifespan_years")

        if not cost or remaining is None:
            continue

        if remaining <= 0:
            # Past expected lifespan
            plans.append(
                {
                    "name": a["name"],
                    "category": a["category"],
                    "replacement_cost": cost,
                    "remaining_years": 0,
                    "monthly_savings": cost,  # Need full amount now
                    "annual_savings": cost,
                    "urgency": "OVERDUE",
                }
            )
        elif remaining <= 2:
            monthly = round(cost / (remaining * 12), 2)
            plans.append(
                {
                    "name": a["name"],
                    "category": a["category"],
                    "replacement_cost": cost,
                    "remaining_years": remaining,
                    "monthly_savings": monthly,
                    "annual_savings": round(monthly * 12, 2),
                    "urgency": "HIGH",
                }
            )
        elif remaining <= 5:
            monthly = round(cost / (remaining * 12), 2)
            plans.append(
                {
                    "name": a["name"],
                    "category": a["category"],
                    "replacement_cost": cost,
                    "remaining_years": remaining,
                    "monthly_savings": monthly,
                    "annual_savings": round(monthly * 12, 2),
                    "urgency": "MEDIUM",
                }
            )
        else:
            monthly = round(cost / (remaining * 12), 2)
            plans.append(
                {
                    "name": a["name"],
                    "category": a["category"],
                    "replacement_cost": cost,
                    "remaining_years": remaining,
                    "monthly_savings": monthly,
                    "annual_savings": round(monthly * 12, 2),
                    "urgency": "LOW",
                }
            )

    # Sort by urgency (OVERDUE first, then HIGH, MEDIUM, LOW)
    urgency_order = {"OVERDUE": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    plans.sort(key=lambda p: urgency_order.get(p["urgency"], 4))

    total_monthly = sum(p["monthly_savings"] for p in plans)
    total_annual = sum(p["annual_savings"] for p in plans)

    return {
        "plans": plans,
        "total_monthly_savings": round(total_monthly, 2),
        "total_annual_savings": round(total_annual, 2),
        "count": len(plans),
    }


def upcoming_maintenance_costs(config):
    """Estimate upcoming maintenance costs from task schedule.

    Looks at all active tasks and their typical costs from history
    to forecast near-term spending.

    Returns list of upcoming costs with estimated amounts.
    """
    tasks = list_tasks(config)
    today = date.today()

    upcoming = []
    for t in tasks:
        if not t.get("next_due"):
            continue

        due = date.fromisoformat(t["next_due"])
        days_until = (due - today).days

        # Only show tasks due within 90 days
        if days_until > 90:
            continue

        upcoming.append(
            {
                "name": t["name"],
                "category": t["category"],
                "next_due": t["next_due"],
                "days_until": days_until,
                "overdue": days_until < 0,
            }
        )

    upcoming.sort(key=lambda u: u["days_until"])
    return {"upcoming": upcoming, "count": len(upcoming)}


def utility_budget_recommendation(config):
    """Generate utility budget recommendations based on historical averages.

    Compares current year spending to historical averages and suggests
    monthly budget targets.

    Returns dict with per-type recommendations.
    """
    summary = get_utility_summary(config)

    if not summary:
        return {"recommendations": [], "note": "No utility data available"}

    recs = []
    for s in summary:
        # Recommend 10% buffer over average
        recommended = round(s["avg"] * 1.1, 2)
        recs.append(
            {
                "type": s["type"],
                "avg_monthly": round(s["avg"], 2),
                "min_monthly": round(s["min"], 2),
                "max_monthly": round(s["max"], 2),
                "recommended_budget": recommended,
                "months_of_data": s["months"],
            }
        )

    total_recommended = sum(r["recommended_budget"] for r in recs)
    return {
        "recommendations": recs,
        "total_recommended_monthly": round(total_recommended, 2),
    }


def budget_overview(config):
    """Comprehensive budget planning overview combining all data sources.

    Returns a unified view for Claude to use when discussing budget
    with the user.
    """
    sinking = appliance_sinking_fund_plan(config)
    maintenance = upcoming_maintenance_costs(config)
    utilities = utility_budget_recommendation(config)
    costs = get_cost_summary(config, str(date.today().year))

    ytd_total = sum(c["total"] for c in costs) if costs else 0

    return {
        "appliance_sinking_funds": sinking,
        "upcoming_maintenance": maintenance,
        "utility_recommendations": utilities,
        "ytd_spending": {
            "total": round(ytd_total, 2),
            "by_category": costs,
        },
    }
