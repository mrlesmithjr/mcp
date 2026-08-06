"""Backend-neutral report/list computations over home_log rows (issue #58).

Operates on the canonical row dicts returned by homeops.log_store.read_table()
so the sqlite and markdown backends produce identical MCP tool output from
the same underlying data. This mirrors the filtering, sorting, and
aggregation that used to live directly in homeops.db.tasks,
homeops.db.pest, homeops.db.providers, homeops.db.utilities,
homeops.db.costs, and homeops.db.appliances, plus the composition logic in
homeops.status and homeops.ynab_bridge -- those modules (and their CLI
call sites) are unchanged and still query SQLite directly, so this is
deliberate duplication of their shaping logic against row lists instead of
a live DB connection, not a refactor of them. Mirrors the equivalent
duplication in lawnops' log_compute.py (issue #57).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta


def _fuzzy_contains(haystack: str | None, needle: str) -> bool:
    """Case-insensitive substring match, mirroring SQL `LIKE '%needle%'`."""
    return (needle or "").lower() in (haystack or "").lower()


# ── Tasks ──


def list_tasks(rows: list[dict], category: str | None = None, active_only: bool = True) -> list[dict]:
    """Mirrors homeops.db.tasks.list_tasks. Adds days_until/overdue."""
    filtered = list(rows)
    if active_only:
        filtered = [r for r in filtered if bool(r.get("active"))]
    if category:
        filtered = [r for r in filtered if r.get("category") == category]
    filtered.sort(key=lambda r: (r.get("next_due") is None, r.get("next_due") or "", r.get("name") or ""))

    today = date.today()
    tasks = []
    for r in filtered:
        t = dict(r)
        if t.get("next_due"):
            due = date.fromisoformat(t["next_due"])
            delta = (due - today).days
            t["days_until"] = delta
            t["overdue"] = delta < 0
        else:
            t["days_until"] = None
            t["overdue"] = False
        tasks.append(t)
    return tasks


def get_overdue(rows: list[dict]) -> list[dict]:
    """Mirrors homeops.db.tasks.get_overdue."""
    return [t for t in list_tasks(rows) if t["overdue"]]


def compute_next_due(last_done_str: str | None, interval_days: float) -> str | None:
    """Mirrors homeops.db.tasks._compute_next_due."""
    if not last_done_str:
        return None
    last_done = date.fromisoformat(last_done_str)
    return (last_done + timedelta(days=interval_days)).isoformat()


def match_single_active_task(rows: list[dict], name: str) -> dict:
    """Mirrors the fuzzy-match + uniqueness check in homeops.db.tasks.mark_done."""
    matched = [r for r in rows if bool(r.get("active")) and _fuzzy_contains(r.get("name"), name)]
    if len(matched) == 0:
        raise RuntimeError(f"No active task matching '{name}'.")
    if len(matched) > 1:
        names = [r["name"] for r in matched]
        raise RuntimeError(f"Multiple tasks match '{name}': {', '.join(names)}. Be more specific.")
    return matched[0]


def match_tasks_by_name_and_active(rows: list[dict], name: str, active: bool) -> list[dict]:
    """Mirrors the bulk `WHERE name LIKE ? AND active = ?` used by
    homeops.db.tasks.pause_task/resume_task -- every matching task (not just
    one), since the original SQL has no uniqueness requirement here."""
    return [r for r in rows if bool(r.get("active")) == active and _fuzzy_contains(r.get("name"), name)]


def task_history(task_log_rows: list[dict], task_name: str) -> list[dict]:
    """Mirrors homeops.db.tasks.get_task_history. task_log rows carry the
    task's name directly in the `task` column (see log_schema.py); output
    keys mirror the original `tl.*, t.name as task_name` join shape exactly,
    including `task_id`/`created_at` -- sqlite-populated passthrough
    metadata (real value on sqlite, None on markdown; never stored in the
    markdown table itself, see log_schema.EntitySchema.passthrough)."""
    matched = [r for r in task_log_rows if _fuzzy_contains(r.get("task"), task_name)]
    matched.sort(key=lambda r: r.get("date") or "", reverse=True)
    return [
        {
            "id": r.get("id"),
            "task_id": r.get("task_id"),
            "date": r.get("date"),
            "cost": r.get("cost"),
            "provider": r.get("provider"),
            "notes": r.get("notes"),
            "created_at": r.get("created_at"),
            "task_name": r.get("task"),
        }
        for r in matched
    ]


# ── Pest ──


def list_pest_history(rows: list[dict], year: str | None = None, limit: int = 25) -> list[dict]:
    """Mirrors homeops.db.pest.list_pest_history."""
    filtered = rows
    if year:
        prefix = str(year)
        filtered = [r for r in filtered if str(r.get("date") or "").startswith(prefix)]
    filtered = sorted(filtered, key=lambda r: r.get("date") or "", reverse=True)
    return filtered[:limit]


# ── Providers ──


def list_providers(rows: list[dict], category: str | None = None, active_only: bool = True) -> list[dict]:
    """Mirrors homeops.db.providers.list_providers."""
    filtered = list(rows)
    if active_only:
        filtered = [r for r in filtered if bool(r.get("active"))]
    if category:
        filtered = [r for r in filtered if r.get("category") == category]
    filtered.sort(key=lambda r: (r.get("category") or "", r.get("name") or ""))
    return filtered


def find_provider(rows: list[dict], name: str) -> dict | None:
    """First fuzzy-name match in row order, mirroring the unordered
    `SELECT * FROM providers WHERE name LIKE ?` -> rows[0] in
    homeops.db.providers.get_provider_detail."""
    for r in rows:
        if _fuzzy_contains(r.get("name"), name):
            return r
    return None


def provider_cost_history(costs_rows: list[dict], provider_name: str, limit: int = 20) -> list[dict]:
    """Mirrors the costs lookup in homeops.db.providers.get_provider_detail."""
    matched = [r for r in costs_rows if _fuzzy_contains(r.get("provider"), provider_name)]
    matched.sort(key=lambda r: r.get("date") or "", reverse=True)
    return [
        {
            "date": r.get("date"),
            "amount": r.get("amount"),
            "description": r.get("description"),
            "notes": r.get("notes"),
        }
        for r in matched[:limit]
    ]


def get_provider_detail(providers_rows: list[dict], costs_rows: list[dict], name: str) -> dict | None:
    """Mirrors homeops.db.providers.get_provider_detail."""
    provider = find_provider(providers_rows, name)
    if provider is None:
        return None
    detail = dict(provider)
    detail["cost_history"] = provider_cost_history(costs_rows, provider.get("name") or "", limit=20)
    return detail


# ── Utilities ──


def get_utility_trend(rows: list[dict], utility_type: str, months: int = 12) -> list[dict]:
    """Mirrors homeops.db.utilities.get_utility_trend (chronological order)."""
    utility_type = (utility_type or "").lower()
    filtered = [r for r in rows if (r.get("type") or "").lower() == utility_type]
    filtered.sort(key=lambda r: r.get("bill_date") or "", reverse=True)
    result = filtered[:months]
    result.reverse()
    return result


def get_utility_summary(rows: list[dict], year: str | None = None) -> list[dict]:
    """Mirrors homeops.db.utilities.get_utility_summary. A type's total/avg/
    min/max are None only if every bill's amount is None (SQL SUM/AVG/MIN/MAX
    ignore NULLs; `amount` is NOT NULL in the sqlite schema, so this only
    matters for a hand-edited markdown row)."""
    filtered = rows
    if year:
        prefix = str(year)
        filtered = [r for r in filtered if str(r.get("bill_date") or "").startswith(prefix)]

    groups: dict = defaultdict(list)
    for r in filtered:
        groups[r.get("type")].append(r.get("amount"))

    summary = []
    for utility_type, amounts in groups.items():
        present = [a for a in amounts if a is not None]
        summary.append(
            {
                "type": utility_type,
                "months": len(amounts),
                "total": sum(present) if present else None,
                "avg": (sum(present) / len(present)) if present else None,
                "min": min(present) if present else None,
                "max": max(present) if present else None,
            }
        )
    summary.sort(key=lambda s: (s["total"] is None, -(s["total"] or 0)))
    return summary


# ── Costs ──


def get_cost_summary(rows: list[dict], year: str | None = None) -> list[dict]:
    """Mirrors homeops.db.costs.get_cost_summary. See get_utility_summary's
    NULL-handling note -- same reasoning applies here."""
    filtered = rows
    if year:
        prefix = str(year)
        filtered = [r for r in filtered if str(r.get("date") or "").startswith(prefix)]

    groups: dict = defaultdict(list)
    for r in filtered:
        groups[r.get("category")].append(r.get("amount"))

    summary = []
    for category, amounts in groups.items():
        present = [a for a in amounts if a is not None]
        summary.append(
            {
                "category": category,
                "count": len(amounts),
                "total": sum(present) if present else None,
                "avg": (sum(present) / len(present)) if present else None,
                "min": min(present) if present else None,
                "max": max(present) if present else None,
            }
        )
    summary.sort(key=lambda s: (s["total"] is None, -(s["total"] or 0)))
    return summary


def get_cost_history(
    rows: list[dict], year: str | None = None, category: str | None = None, limit: int = 50
) -> list[dict]:
    """Mirrors homeops.db.costs.get_cost_history."""
    filtered = rows
    if year:
        prefix = str(year)
        filtered = [r for r in filtered if str(r.get("date") or "").startswith(prefix)]
    if category:
        filtered = [r for r in filtered if r.get("category") == category]
    filtered = sorted(filtered, key=lambda r: r.get("date") or "", reverse=True)
    return filtered[:limit]


# ── Appliances ──


def _parse_appliance_date(date_str: str | None):
    """Mirrors homeops.db.appliances._parse_date (YYYY-MM-DD or YYYY-MM)."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        pass
    try:
        parts = date_str.split("-")
        if len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
    except (ValueError, IndexError):
        pass
    return None


def list_appliances(rows: list[dict], category: str | None = None) -> list[dict]:
    """Mirrors homeops.db.appliances.list_appliances, including its
    `expected_lifespan_years and age_years` truthy check (age_years == 0.0
    short-circuits to no remaining-lifespan estimate) -- kept as-is for
    behavioral parity with the pre-#58 sqlite-only implementation, not fixed
    here."""
    filtered = list(rows)
    if category:
        filtered = [r for r in filtered if r.get("category") == category]
    filtered.sort(key=lambda r: (r.get("category") or "", r.get("name") or ""))

    today = date.today()
    appliances = []
    for r in filtered:
        a = dict(r)

        if a.get("purchase_date"):
            purchased = _parse_appliance_date(a["purchase_date"])
            a["age_years"] = round((today - purchased).days / 365.25, 1) if purchased else None
        else:
            a["age_years"] = None

        if a.get("warranty_end"):
            warranty = _parse_appliance_date(a["warranty_end"])
            if warranty:
                a["warranty_active"] = warranty >= today
                a["warranty_days_left"] = (warranty - today).days if warranty >= today else 0
            else:
                a["warranty_active"] = None
                a["warranty_days_left"] = None
        else:
            a["warranty_active"] = None
            a["warranty_days_left"] = None

        if a.get("expected_lifespan_years") and a.get("age_years"):
            remaining = a["expected_lifespan_years"] - a["age_years"]
            a["remaining_lifespan_years"] = round(max(0, remaining), 1)
        else:
            a["remaining_lifespan_years"] = None

        appliances.append(a)

    return appliances


def get_expiring_warranties(appliances_rows: list[dict], months: int = 12) -> list[dict]:
    """Mirrors homeops.db.appliances.get_expiring_warranties. Takes the
    already-shaped output of list_appliances (matches the original, which
    calls list_appliances(config) internally first)."""
    max_days = months * 30
    return [a for a in appliances_rows if a.get("warranty_active") and a.get("warranty_days_left", 999) <= max_days]


def get_aging_appliances(appliances_rows: list[dict], threshold_years: float = 2) -> list[dict]:
    """Mirrors homeops.db.appliances.get_aging_appliances."""
    return [
        a
        for a in appliances_rows
        if a.get("remaining_lifespan_years") is not None and a["remaining_lifespan_years"] <= threshold_years
    ]


# ── Budget planning (mirrors homeops.ynab_bridge) ──


def appliance_sinking_fund_plan(appliances_rows: list[dict]) -> dict:
    """Mirrors homeops.ynab_bridge.appliance_sinking_fund_plan. Takes the
    already-shaped output of list_appliances (needs remaining_lifespan_years)."""
    plans = []

    for a in appliances_rows:
        cost = a.get("replacement_cost")
        remaining = a.get("remaining_lifespan_years")

        if not cost or remaining is None:
            continue

        if remaining <= 0:
            plans.append(
                {
                    "name": a["name"],
                    "category": a["category"],
                    "replacement_cost": cost,
                    "remaining_years": 0,
                    "monthly_savings": cost,
                    "annual_savings": cost,
                    "urgency": "OVERDUE",
                }
            )
            continue

        monthly = round(cost / (remaining * 12), 2)
        if remaining <= 2:
            urgency = "HIGH"
        elif remaining <= 5:
            urgency = "MEDIUM"
        else:
            urgency = "LOW"
        plans.append(
            {
                "name": a["name"],
                "category": a["category"],
                "replacement_cost": cost,
                "remaining_years": remaining,
                "monthly_savings": monthly,
                "annual_savings": round(monthly * 12, 2),
                "urgency": urgency,
            }
        )

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


def upcoming_maintenance_costs(tasks_rows: list[dict]) -> dict:
    """Mirrors homeops.ynab_bridge.upcoming_maintenance_costs. Takes the
    already-shaped output of list_tasks (needs next_due/days_until)."""
    today = date.today()
    upcoming = []
    for t in tasks_rows:
        if not t.get("next_due"):
            continue
        due = date.fromisoformat(t["next_due"])
        days_until = (due - today).days
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


def utility_budget_recommendation(summary_rows: list[dict]) -> dict:
    """Mirrors homeops.ynab_bridge.utility_budget_recommendation. Takes the
    already-computed output of get_utility_summary."""
    if not summary_rows:
        return {"recommendations": [], "note": "No utility data available"}

    recs = []
    for s in summary_rows:
        recommended = round((s["avg"] or 0) * 1.1, 2)
        recs.append(
            {
                "type": s["type"],
                "avg_monthly": round(s["avg"] or 0, 2),
                "min_monthly": round(s["min"] or 0, 2),
                "max_monthly": round(s["max"] or 0, 2),
                "recommended_budget": recommended,
                "months_of_data": s["months"],
            }
        )

    total_recommended = sum(r["recommended_budget"] for r in recs)
    return {
        "recommendations": recs,
        "total_recommended_monthly": round(total_recommended, 2),
    }
