"""Status dashboard - unified view of all homeops data.

Routed through homeops.log_store/log_compute (issue #58) so `home_status`
(both the MCP tool and the `homeops status` CLI command, which share this
module) reflects whichever `home_log.backend` is configured, instead of
always reading SQLite directly.
"""

from datetime import date

from homeops import log_compute, log_store


def get_status(config):
    """Get a comprehensive status overview of all homeops data."""
    today = date.today()
    year = str(today.year)

    tasks = log_compute.list_tasks(log_store.read_table(config, "tasks"))
    overdue = [t for t in tasks if t.get("overdue")]
    due_soon = [t for t in tasks if not t.get("overdue") and t.get("days_until") is not None and t["days_until"] <= 14]

    appliances = log_compute.list_appliances(log_store.read_table(config, "appliances"))
    expiring_warranties = log_compute.get_expiring_warranties(appliances)
    aging = log_compute.get_aging_appliances(appliances)

    pest_recent = log_compute.list_pest_history(log_store.read_table(config, "pest_treatments"), limit=5)
    providers = log_compute.list_providers(log_store.read_table(config, "providers"))
    cost_summary = log_compute.get_cost_summary(log_store.read_table(config, "costs"), year)
    utility_summary = log_compute.get_utility_summary(log_store.read_table(config, "utility_bills"), year)

    ytd_costs = sum(c["total"] or 0 for c in cost_summary) if cost_summary else 0

    return {
        "date": today.isoformat(),
        "tasks": {
            "total": len(tasks),
            "overdue": overdue,
            "overdue_count": len(overdue),
            "due_soon": due_soon,
            "due_soon_count": len(due_soon),
        },
        "appliances": {
            "total": len(appliances),
            "expiring_warranties": expiring_warranties,
            "expiring_count": len(expiring_warranties),
            "aging": aging,
            "aging_count": len(aging),
        },
        "pest": {
            "recent_treatments": pest_recent,
            "recent_count": len(pest_recent),
        },
        "providers": {
            "total": len(providers),
        },
        "costs": {
            "ytd_total": round(ytd_costs, 2),
            "by_category": cost_summary,
        },
        "utilities": {
            "summary": utility_summary,
        },
    }
