"""Database operations for HomeOps."""

from homeops.db.appliances import (
    add_appliance,
    get_aging_appliances,
    get_expiring_warranties,
    list_appliances,
)
from homeops.db.connection import get_db, get_db_path
from homeops.db.costs import add_cost, delete_cost, get_cost_history, get_cost_summary
from homeops.db.hvac import (
    get_hvac_efficiency,
    get_hvac_trend,
    get_mode_distribution,
    record_snapshot,
)
from homeops.db.pest import add_pest_treatment, delete_pest_treatment, list_pest_history
from homeops.db.providers import add_provider, get_provider_detail, list_providers
from homeops.db.schema import init_db
from homeops.db.tasks import (
    add_task,
    delete_task,
    evaluate_task_escalation,
    get_overdue,
    get_task_history,
    list_tasks,
    mark_done,
    pause_task,
    resume_task,
)
from homeops.db.utilities import (
    add_utility_bill,
    delete_utility_bill,
    evaluate_utility_anomalies,
    get_all_utility_history,
    get_utility_summary,
    get_utility_trend,
)

__all__ = [
    "get_db_path",
    "get_db",
    "init_db",
    "add_task",
    "list_tasks",
    "mark_done",
    "pause_task",
    "resume_task",
    "get_overdue",
    "evaluate_task_escalation",
    "get_task_history",
    "delete_task",
    "add_pest_treatment",
    "list_pest_history",
    "delete_pest_treatment",
    "add_provider",
    "list_providers",
    "get_provider_detail",
    "add_cost",
    "get_cost_summary",
    "get_cost_history",
    "delete_cost",
    "add_appliance",
    "list_appliances",
    "get_expiring_warranties",
    "get_aging_appliances",
    "add_utility_bill",
    "get_utility_trend",
    "get_utility_summary",
    "get_all_utility_history",
    "delete_utility_bill",
    "evaluate_utility_anomalies",
    "record_snapshot",
    "get_hvac_trend",
    "get_mode_distribution",
    "get_hvac_efficiency",
]
