"""MCP server exposing homeops data as callable tools for Claude Code.

Returns structured JSON optimized for LLM consumption.
"""

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "homeops",
    instructions=(
        "Check task_list before adding new tasks to avoid duplicates. "
        "Use task_done to mark tasks complete instead of task_delete - this preserves history."
    ),
)

# MCP tool annotations follow the workspace convention in the root CLAUDE.md.
# openWorldHint is set explicitly because the spec default is true (open world);
# all homeops tools touch only local SQLite state or a local LAN Home Assistant
# instance (not a remote third-party cloud API), so all are closed-world.
# destructiveHint defaults to true, so reversible writes must override it False.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
_DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

# ── Config ──

_config_cache = None


def _config():
    """Load config once per server lifetime."""
    global _config_cache
    if _config_cache is None:
        from homeops.config import load_config

        _config_cache = load_config()
    return _config_cache


# ── Status ──


@mcp.tool(annotations=_READ_ONLY)
def home_status() -> str:
    """Comprehensive homeops dashboard - overdue tasks, appliance alerts,
    recent pest treatments, YTD spending, and provider count.

    Returns JSON: {date, tasks: {total, overdue, due_soon},
    appliances: {total, expiring, aging}, pest: {recent_treatments},
    costs: {ytd_total, by_category}, providers: {total}}
    """
    try:
        from homeops.status import get_status

        data = get_status(_config())
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tasks ──


@mcp.tool(annotations=_READ_ONLY)
def task_list() -> str:
    """List all active recurring maintenance tasks with status, due dates, and overdue flags.

    Returns JSON: {tasks: [{name, category, interval_days, last_done, next_due,
    days_until, overdue, notes, active}]}
    """
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "tasks")
        tasks = log_compute.list_tasks(rows)
        return json.dumps({"tasks": tasks, "count": len(tasks)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def task_overdue() -> str:
    """Show only overdue maintenance tasks.

    Returns JSON: {tasks: [{name, category, next_due, days_until, overdue}]}
    """
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "tasks")
        tasks = log_compute.get_overdue(rows)
        return json.dumps({"tasks": tasks, "count": len(tasks)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def task_history(task_name: str) -> str:
    """Show completion history for a specific task."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "task_log")
        history = log_compute.task_history(rows, task_name)
        return json.dumps({"history": history, "count": len(history)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_WRITE)
def task_done(name: str, date: str = None, cost: float = None, provider: str = None, notes: str = None) -> str:
    """Mark a recurring maintenance task as completed."""
    try:
        from datetime import date as _date

        from homeops import log_compute, log_store

        config = _config()
        tasks_rows = log_store.read_table(config, "tasks")
        task = log_compute.match_single_active_task(tasks_rows, name)

        done_date = date if date is not None else _date.today().isoformat()
        next_due = log_compute.compute_next_due(done_date, task["interval_days"])

        log_store.update_row(config, "tasks", {"id": task["id"]}, {"last_done": done_date, "next_due": next_due})
        task_log_row = log_store.append_row(
            config,
            "task_log",
            {"task": task["name"], "date": done_date, "cost": cost, "provider": provider, "notes": notes},
        )
        if cost and cost > 0:
            # source_id mirrors pre-#58 mark_done's `last_insert_rowid()`
            # of the task_log row just appended above (sqlite only -- the
            # markdown backend's append_row doesn't return a stable id, so
            # this is None there, matching costs.source_id's None passthrough).
            log_store.append_row(
                config,
                "costs",
                {
                    "date": done_date,
                    "category": task["category"],
                    "amount": cost,
                    "provider": provider,
                    "description": task["name"],
                    "source": "task_log",
                    "source_id": task_log_row.get("id"),
                    "notes": None,
                },
            )

        return json.dumps(
            {"name": task["name"], "done_date": done_date, "next_due": next_due, "cost": cost, "provider": provider}
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_WRITE)
def task_add(name: str, category: str, interval: str, notes: str = None) -> str:
    """Add a new recurring maintenance task."""
    try:
        from homeops import log_store
        from homeops.db.tasks import parse_interval

        interval_days = parse_interval(interval)
        log_store.append_row(
            _config(),
            "tasks",
            {"name": name, "category": category, "interval_days": interval_days, "notes": notes},
        )
        return json.dumps({"name": name, "category": category, "interval_days": interval_days})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def task_pause(name: str) -> str:
    """Pause (deactivate) a recurring task without deleting it."""
    try:
        from homeops import log_compute, log_store

        config = _config()
        rows = log_store.read_table(config, "tasks")
        matched = log_compute.match_tasks_by_name_and_active(rows, name, True)
        if not matched:
            raise RuntimeError(f"No active task matching '{name}'.")
        for t in matched:
            log_store.update_row(config, "tasks", {"id": t["id"]}, {"active": 0})
        return json.dumps({"name": name, "paused": True})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def task_resume(name: str) -> str:
    """Resume a previously paused recurring task."""
    try:
        from homeops import log_compute, log_store

        config = _config()
        rows = log_store.read_table(config, "tasks")
        matched = log_compute.match_tasks_by_name_and_active(rows, name, False)
        if not matched:
            raise RuntimeError(f"No paused task matching '{name}'.")
        for t in matched:
            log_store.update_row(config, "tasks", {"id": t["id"]}, {"active": 1})
        return json.dumps({"name": name, "resumed": True})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_DELETE)
def task_delete(name: str) -> str:
    """Delete a recurring task by name (partial match)."""
    try:
        from homeops import log_store

        rowcount = log_store.delete_row(_config(), "tasks", {"name_contains": name})
        result = {"name": name, "deleted": rowcount}
        if rowcount == 0:
            result["warning"] = f"No task found matching '{name}'"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Pest Control ──


@mcp.tool(annotations=_READ_ONLY)
def pest_history(year: str = None) -> str:
    """View pest control treatment history."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "pest_treatments")
        treatments = log_compute.list_pest_history(rows, year)
        return json.dumps({"treatments": treatments, "count": len(treatments)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_WRITE)
def pest_add(date: str, area: str, product: str, method: str = None, notes: str = None, cost: float = None) -> str:
    """Log a pest control treatment."""
    try:
        from homeops import log_store

        config = _config()
        log_store.append_row(
            config,
            "pest_treatments",
            {"date": date, "area": area, "product": product, "method": method, "notes": notes, "cost": cost},
        )
        # Mirrors homeops.db.pest.add_pest_treatment's dual-write into the
        # unified costs ledger, orchestrated here (not inside the sqlite
        # backend) so both backends behave identically -- see
        # log_backends/sqlite_backend.py's module docstring.
        if cost and cost > 0:
            log_store.append_row(
                config,
                "costs",
                {
                    "date": date,
                    "category": "pest",
                    "amount": cost,
                    "provider": None,
                    "description": f"{product} - {area}",
                    "source": "pest_treatment",
                    "notes": None,
                },
            )
        return json.dumps({"date": date, "area": area, "product": product, "method": method, "cost": cost})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_DELETE)
def pest_delete(id: int) -> str:
    """Delete a pest treatment by ID."""
    try:
        from homeops import log_store

        deleted = bool(log_store.delete_row(_config(), "pest_treatments", {"id": id}))
        result = {"id": id, "deleted": deleted}
        if not deleted:
            result["warning"] = f"No pest treatment found with id {id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Providers ──


@mcp.tool(annotations=_READ_ONLY)
def provider_list(category: str = None) -> str:
    """List service providers with contact info and typical costs."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "providers")
        providers = log_compute.list_providers(rows, category)
        return json.dumps({"providers": providers, "count": len(providers)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def provider_detail(name: str) -> str:
    """Show detailed provider info with cost history."""
    try:
        from homeops import log_compute, log_store

        config = _config()
        providers_rows = log_store.read_table(config, "providers")
        costs_rows = log_store.read_table(config, "costs")
        provider = log_compute.get_provider_detail(providers_rows, costs_rows, name)
        if provider is None:
            return json.dumps({"error": f"No provider matching '{name}'"})
        return json.dumps(provider)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Appliances ──


@mcp.tool(annotations=_READ_ONLY)
def appliance_list(category: str = None) -> str:
    """List all registered appliances with age, warranty status, and remaining lifespan."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "appliances")
        appliances = log_compute.list_appliances(rows, category)
        return json.dumps({"appliances": appliances, "count": len(appliances)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def appliance_alerts() -> str:
    """Show appliances with expiring warranties (12mo) or nearing end of life (2yr).

    Returns JSON: {expiring_warranties: [...], aging: [...]}
    """
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "appliances")
        appliances = log_compute.list_appliances(rows)
        expiring = log_compute.get_expiring_warranties(appliances)
        aging = log_compute.get_aging_appliances(appliances)
        return json.dumps(
            {
                "expiring_warranties": expiring,
                "aging": aging,
                "total_alerts": len(expiring) + len(aging),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Utilities ──


@mcp.tool(annotations=_READ_ONLY)
def utility_summary(year: str = None) -> str:
    """Utility bill spending summary by type (water, electric, gas, etc.)."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "utility_bills")
        summary = log_compute.get_utility_summary(rows, year)
        total = sum(s["total"] for s in summary) if summary else 0
        return json.dumps({"summary": summary, "total": total, "year": year})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def utility_trend(utility_type: str, months: int = 12) -> str:
    """Monthly trend for a specific utility type."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "utility_bills")
        bills = log_compute.get_utility_trend(rows, utility_type, months)
        total = sum(b["amount"] for b in bills) if bills else 0
        avg = total / len(bills) if bills else 0
        return json.dumps(
            {
                "type": utility_type,
                "bills": bills,
                "count": len(bills),
                "total": round(total, 2),
                "avg": round(avg, 2),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_WRITE)
def utility_add(date: str, utility_type: str, amount: float, notes: str = None) -> str:
    """Log a monthly utility bill."""
    try:
        from homeops import log_store
        from homeops.db.utilities import VALID_TYPES

        utility_type = utility_type.lower()
        if utility_type not in VALID_TYPES:
            raise RuntimeError(f"Invalid utility type '{utility_type}'. Valid: {', '.join(VALID_TYPES)}")

        config = _config()
        log_store.append_row(
            config,
            "utility_bills",
            {"bill_date": date, "type": utility_type, "amount": amount, "usage": None, "notes": notes},
        )
        # Mirrors homeops.db.utilities.add_utility_bill's dual-write into the
        # unified costs ledger, orchestrated here so both backends behave
        # identically -- see log_backends/sqlite_backend.py's module docstring.
        log_store.append_row(
            config,
            "costs",
            {
                "date": date + "-01",
                "category": f"utility:{utility_type}",
                "amount": amount,
                "provider": None,
                "description": f"{utility_type} bill",
                "source": "utility",
                "notes": None,
            },
        )
        return json.dumps({"date": date, "type": utility_type, "amount": amount, "usage": None})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_DELETE)
def utility_delete(id: int) -> str:
    """Delete a utility bill by ID."""
    try:
        from homeops import log_store

        deleted = bool(log_store.delete_row(_config(), "utility_bills", {"id": id}))
        result = {"id": id, "deleted": deleted}
        if not deleted:
            result["warning"] = f"No utility bill found with id {id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Costs ──


@mcp.tool(annotations=_READ_ONLY)
def cost_summary(year: str = None) -> str:
    """Home maintenance spending summary by category."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "costs")
        summary = log_compute.get_cost_summary(rows, year)
        total = sum(s["total"] for s in summary) if summary else 0
        return json.dumps({"summary": summary, "total": total, "year": year})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def cost_history(year: str = None, category: str = None) -> str:
    """Home maintenance cost line items."""
    try:
        from homeops import log_compute, log_store

        rows = log_store.read_table(_config(), "costs")
        costs = log_compute.get_cost_history(rows, year, category)
        return json.dumps({"costs": costs, "count": len(costs)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_WRITE)
def cost_add(date: str, category: str, amount: float, description: str, provider: str = None, notes: str = None) -> str:
    """Log a home maintenance cost."""
    try:
        from homeops import log_store

        log_store.append_row(
            _config(),
            "costs",
            {
                "date": date,
                "category": category,
                "amount": amount,
                "provider": provider,
                "description": description,
                "notes": notes,
            },
        )
        return json.dumps({"date": date, "category": category, "amount": amount, "provider": provider})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_DELETE)
def cost_delete(id: int) -> str:
    """Delete a cost entry by ID."""
    try:
        from homeops import log_store

        deleted = bool(log_store.delete_row(_config(), "costs", {"id": id}))
        result = {"id": id, "deleted": deleted}
        if not deleted:
            result["warning"] = f"No cost entry found with id {id}"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Budget Planning ──


@mcp.tool(annotations=_READ_ONLY)
def budget_overview() -> str:
    """Comprehensive home budget planning overview - appliance sinking funds,
    upcoming maintenance, utility recommendations, and YTD spending.

    Returns JSON: {appliance_sinking_funds, upcoming_maintenance,
    utility_recommendations, ytd_spending}
    """
    try:
        from homeops.ynab_bridge import budget_overview as _budget_overview

        data = _budget_overview(_config())
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def sinking_fund_plan() -> str:
    """Appliance replacement sinking fund plan - monthly savings needed
    per appliance based on remaining lifespan and replacement cost.

    Returns JSON: {plans: [{name, replacement_cost, remaining_years,
    monthly_savings, urgency}], total_monthly_savings}
    """
    try:
        from homeops.ynab_bridge import appliance_sinking_fund_plan

        data = appliance_sinking_fund_plan(_config())
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── HVAC ──


@mcp.tool(annotations=_READ_ONLY)
def hvac_status() -> str:
    """Current HVAC status for all 3 Ecobee zones - mode, temps, setpoints,
    outdoor conditions, and issues detected (wrong mode for conditions, etc.).
    Sourced from Prometheus (Home Assistant's climate metrics exporter), not
    Home Assistant directly - homeops and Home Assistant never call each
    other (issue #143).

    Returns JSON: {zones: [{name, mode, current_temp, target_temp,
    target_high, target_low, humidity}], outdoor: {temp, humidity},
    issues: [str]}

    `preset` and `fan_mode` are not included: no Prometheus equivalent
    exists for thermostat preset, and fan_mode was dropped alongside it
    rather than exposing one of the two. `target_high`/`target_low` are
    always None: Home Assistant's Prometheus exporter only emits a single
    setpoint metric, with no dual-setpoint (heat_cool) equivalent.
    """
    try:
        from homeops.ha import hvac_status as _hvac_status

        data = _hvac_status()
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def hvac_history(hours: int = 24) -> str:
    """HVAC history - mode changes, temperature adjustments, and detected
    manual overrides for all zones. Reconstructed by diffing Prometheus
    range-query samples (issue #143), not Home Assistant's discrete
    state-change events, so the manual-override heuristic (two changes
    within 5 minutes) only resolves to Prometheus's ~60s scrape interval -
    two real changes inside the same scrape window collapse into one
    sample and become undetectable.
    """
    try:
        from homeops.ha import hvac_history as _hvac_history

        data = _hvac_history(hours)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def hvac_trend(zone: str = None, days: int = 14) -> str:
    """Hourly HVAC trend - avg indoor temp, outdoor temp, humidity, and estimated
    active minutes per zone over the past N days. Requires snapshot data collected
    via 'homeops hvac snapshot' cron.

    Returns JSON: [{hour, zone, avg_temp, avg_outdoor_temp, max_humidity, active_minutes}]
    """
    try:
        from homeops.db.hvac import get_hvac_trend

        data = get_hvac_trend(_config(), zone, days)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def hvac_mode_distribution(zone: str = None, days: int = 30) -> str:
    """HVAC mode breakdown - percentage of time in each mode and estimated runtime
    minutes per zone over the past N days.

    Returns JSON: [{zone, hvac_mode, snapshot_count, pct, estimated_minutes}]
    """
    try:
        from homeops.db.hvac import get_mode_distribution

        data = get_mode_distribution(_config(), zone, days)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(annotations=_READ_ONLY)
def hvac_efficiency(months: int = 3) -> str:
    """Correlate estimated HVAC runtime with electric utility bills by month and zone.
    Useful for evaluating whether runtime changes track with bill changes.

    Returns JSON: [{month, zone, active_minutes, avg_outdoor_temp, electric_bill}]
    """
    try:
        from homeops.db.hvac import get_hvac_efficiency

        data = get_hvac_efficiency(_config(), months)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Entry Point ──


def main():
    """Run the MCP server."""
    logger.info("Starting HomeOps MCP server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
