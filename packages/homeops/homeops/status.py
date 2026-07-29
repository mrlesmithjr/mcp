"""Status dashboard - unified view of all homeops data."""

from datetime import date

from homeops.db.appliances import get_aging_appliances, get_expiring_warranties, list_appliances
from homeops.db.costs import get_cost_summary
from homeops.db.pest import list_pest_history
from homeops.db.providers import list_providers
from homeops.db.tasks import list_tasks
from homeops.db.utilities import get_utility_summary


def get_status(config):
    """Get a comprehensive status overview of all homeops data."""
    today = date.today()
    year = str(today.year)

    tasks = list_tasks(config)
    overdue = [t for t in tasks if t.get("overdue")]
    due_soon = [t for t in tasks if not t.get("overdue") and t.get("days_until") is not None and t["days_until"] <= 14]

    appliances = list_appliances(config)
    expiring_warranties = get_expiring_warranties(config)
    aging = get_aging_appliances(config)

    pest_recent = list_pest_history(config, limit=5)
    providers = list_providers(config)
    cost_summary = get_cost_summary(config, year)
    utility_summary = get_utility_summary(config, year)

    ytd_costs = sum(c["total"] for c in cost_summary) if cost_summary else 0

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
