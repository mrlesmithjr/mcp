"""Recurring task management."""

import re
from datetime import date, timedelta

from homeops.db.connection import get_db


def parse_interval(interval_str):
    """Parse interval string like '30d', '6m', '1y' to integer days."""
    interval_str = interval_str.strip().lower()
    match = re.match(r"^(\d+)\s*(d|m|y)$", interval_str)
    if not match:
        # Try bare integer as days
        try:
            return int(interval_str)
        except ValueError:
            raise RuntimeError(
                f"Invalid interval '{interval_str}'. Use format like 30d, 90d, 6m, 1y, or bare number of days."
            )
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return value
    elif unit == "m":
        return value * 30
    elif unit == "y":
        return value * 365
    return value


def _compute_next_due(last_done_str, interval_days):
    """Compute next due date from last done + interval."""
    if not last_done_str:
        return None
    last_done = date.fromisoformat(last_done_str)
    return (last_done + timedelta(days=interval_days)).isoformat()


def add_task(config, name, category, interval_str, notes=None):
    """Add a recurring task definition."""
    conn = get_db(config)
    interval_days = parse_interval(interval_str)

    try:
        conn.execute(
            "INSERT INTO tasks (name, category, interval_days, notes) VALUES (?, ?, ?, ?)",
            (name, category, interval_days, notes),
        )
        conn.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise RuntimeError(f"Task '{name}' already exists.")
        raise
    finally:
        conn.close()

    return {"name": name, "category": category, "interval_days": interval_days}


def list_tasks(config, category=None, active_only=True):
    """List all task definitions with status."""
    conn = get_db(config)
    query = "SELECT * FROM tasks"
    params = []
    conditions = []

    if active_only:
        conditions.append("active = 1")
    if category:
        conditions.append("category = ?")
        params.append(category)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY next_due IS NULL, next_due, name"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    tasks = []
    for row in rows:
        task = dict(row)
        if task["next_due"]:
            due = date.fromisoformat(task["next_due"])
            delta = (due - date.today()).days
            task["days_until"] = delta
            task["overdue"] = delta < 0
        else:
            task["days_until"] = None
            task["overdue"] = False
        tasks.append(task)

    return tasks


def get_overdue(config):
    """Get only overdue tasks."""
    tasks = list_tasks(config)
    return [t for t in tasks if t["overdue"]]


def evaluate_task_escalation(config, non_safety_threshold_days=60):
    """Flag overdue tasks that qualify for escalation: any safety-category task (any amount), or any non-safety task overdue more than `non_safety_threshold_days` (issue #146)."""
    qualifying = []
    for t in get_overdue(config):
        days_overdue = abs(t["days_until"])
        if t["category"] == "safety":
            reason = "safety"
        elif days_overdue > non_safety_threshold_days:
            reason = f"{non_safety_threshold_days}+ days"
        else:
            continue
        qualifying.append(
            {
                "name": t["name"],
                "category": t["category"],
                "days_overdue": days_overdue,
                "reason": reason,
            }
        )
    return qualifying


def mark_done(config, name, cost=None, provider=None, notes=None, done_date=None):
    """Mark a task as done. Updates next_due and logs completion."""
    conn = get_db(config)
    # Fuzzy match task name
    rows = conn.execute(
        "SELECT * FROM tasks WHERE name LIKE ? AND active = 1",
        (f"%{name}%",),
    ).fetchall()

    if len(rows) == 0:
        conn.close()
        raise RuntimeError(f"No active task matching '{name}'.")
    if len(rows) > 1:
        names = [r["name"] for r in rows]
        conn.close()
        raise RuntimeError(f"Multiple tasks match '{name}': {', '.join(names)}. Be more specific.")

    task = dict(rows[0])
    if done_date is None:
        done_date = date.today().isoformat()

    next_due = _compute_next_due(done_date, task["interval_days"])

    # Update task
    conn.execute(
        "UPDATE tasks SET last_done = ?, next_due = ? WHERE id = ?",
        (done_date, next_due, task["id"]),
    )

    # Log completion
    conn.execute(
        "INSERT INTO task_log (task_id, date, cost, provider, notes) VALUES (?, ?, ?, ?, ?)",
        (task["id"], done_date, cost, provider, notes),
    )

    # If cost provided, also log to costs table
    if cost and cost > 0:
        conn.execute(
            "INSERT INTO costs (date, category, amount, provider, description, source, source_id) "
            "VALUES (?, ?, ?, ?, ?, 'task_log', last_insert_rowid())",
            (done_date, task["category"], cost, provider, task["name"]),
        )

    conn.commit()
    conn.close()

    return {
        "name": task["name"],
        "done_date": done_date,
        "next_due": next_due,
        "cost": cost,
        "provider": provider,
    }


def pause_task(config, name):
    """Deactivate a task without deleting."""
    conn = get_db(config)
    result = conn.execute(
        "UPDATE tasks SET active = 0 WHERE name LIKE ? AND active = 1",
        (f"%{name}%",),
    )
    conn.commit()
    affected = result.rowcount
    conn.close()

    if affected == 0:
        raise RuntimeError(f"No active task matching '{name}'.")

    return {"name": name, "paused": True}


def resume_task(config, name):
    """Reactivate a paused task."""
    conn = get_db(config)
    result = conn.execute(
        "UPDATE tasks SET active = 1 WHERE name LIKE ? AND active = 0",
        (f"%{name}%",),
    )
    conn.commit()
    affected = result.rowcount
    conn.close()

    if affected == 0:
        raise RuntimeError(f"No paused task matching '{name}'.")
    return {"name": name, "resumed": True}


def delete_task(config, name):
    """Delete task(s) by partial name match. Returns number of rows deleted."""
    conn = get_db(config)
    result = conn.execute("DELETE FROM tasks WHERE name LIKE ?", (f"%{name}%",))
    conn.commit()
    rowcount = result.rowcount
    conn.close()

    return rowcount


def get_task_history(config, name):
    """Get completion log for a specific task."""
    conn = get_db(config)
    rows = conn.execute(
        "SELECT tl.*, t.name as task_name FROM task_log tl "
        "JOIN tasks t ON tl.task_id = t.id "
        "WHERE t.name LIKE ? ORDER BY tl.date DESC",
        (f"%{name}%",),
    ).fetchall()
    conn.close()

    return [dict(r) for r in rows]
