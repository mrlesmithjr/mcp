"""Audit log viewer for all ynab-tools actions."""

from ..db import get_connection, init_db


def run_audit_log(limit: int = 30, action: str | None = None) -> None:
    """Show recent audit log entries including YNAB app budget moves."""
    conn = get_connection()
    try:
        init_db(conn)

        where = "1=1"
        audit_params: list = []
        if action:
            where = "action LIKE ?"
            audit_params.append(f"%{action}%")
        audit_params.append(limit)

        audit_rows = conn.execute(
            f"""
            SELECT timestamp, action, entity_name AS name, details, 'tool' AS row_source
            FROM audit_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            audit_params,
        ).fetchall()

        # Include budget moves made in the YNAB app (money_movements), excluding rows
        # already covered by funding_log to avoid duplicates from CLI-sourced moves.
        # Dedup logic mirrors dashboard/api/audit.py lines 72-100.
        movement_rows = conn.execute(
            """
            SELECT moved_at AS timestamp,
                   'move' AS action,
                   COALESCE(to_category_name, 'RTA') AS name,
                   printf('%s → %s: $%.2f',
                       COALESCE(from_category_name, 'RTA'),
                       COALESCE(to_category_name, 'RTA'),
                       amount) AS details,
                   'ynab' AS row_source
            FROM money_movements mm
            WHERE deleted = 0
              AND NOT EXISTS (
                  SELECT 1 FROM funding_log fl
                  WHERE fl.category_name = mm.to_category_name
                    AND ABS(fl.delta - mm.amount) < 0.01
                    AND ABS(
                        strftime('%s', replace(mm.moved_at, 'Z', '')) -
                        strftime('%s', substr(fl.timestamp, 1, 19))
                    ) < 60
              )
            ORDER BY moved_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        entries = [dict(r) for r in audit_rows] + [dict(r) for r in movement_rows]
        entries.sort(key=lambda e: e["timestamp"], reverse=True)
        entries = entries[:limit]

        if not entries:
            print("No audit log entries found.")
            return

        print(f"Audit Log (last {limit})")
        print("=" * 110)

        for entry in entries:
            ts = entry["timestamp"][:16].replace("T", " ")
            action_str = entry["action"]
            name = entry["name"] or ""
            details = entry["details"] or ""
            tag = f"[{entry['row_source']}]"

            print(f"  {ts}  {tag:<6}  {action_str:<22} {name:<35} {details}")

        print()
        print(f"  {len(entries)} entries shown. Use -n to see more.")
    finally:
        conn.close()
