"""Category management: create, set goals."""

import logging

from ..client import YNABClient
from ..config import require_credentials
from ..db import current_month, get_connection, init_db, log_audit
from ..stats import parse_category_input, strip_emoji_prefix

logger = logging.getLogger(__name__)


def _find_category_group(conn, search: str) -> dict | None:
    """Find a category group by name. Tries exact match first, then substring."""
    # Try exact match first (case-insensitive)
    exact = conn.execute(
        """
        SELECT DISTINCT category_group_id AS id, category_group_name AS name
        FROM budget_categories
        WHERE deleted = 0 AND hidden = 0
          AND LOWER(category_group_name) = LOWER(?)
        ORDER BY category_group_name
    """,
        (search,),
    ).fetchall()

    if len(exact) == 1:
        return dict(exact[0])

    # Fall back to substring match
    rows = conn.execute(
        """
        SELECT DISTINCT category_group_id AS id, category_group_name AS name
        FROM budget_categories
        WHERE deleted = 0 AND hidden = 0
          AND LOWER(category_group_name) LIKE LOWER(?)
        ORDER BY category_group_name
    """,
        (f"%{search}%",),
    ).fetchall()

    if not rows:
        print(f"No category group found matching '{search}'")
        return None
    if len(rows) == 1:
        return dict(rows[0])

    print(f"Multiple category groups match '{search}':")
    for r in rows:
        print(f"  {r['name']}")
    print("\nBe more specific.")
    return None


def run_create_group(name: str, apply: bool = False) -> None:
    """Create a new category group in YNAB."""
    conn = get_connection()
    try:
        init_db(conn)

        # Check if group already exists
        existing = conn.execute(
            """
            SELECT DISTINCT category_group_name AS name
            FROM budget_categories
            WHERE deleted = 0 AND hidden = 0
              AND LOWER(category_group_name) = LOWER(?)
            LIMIT 1
        """,
            (name,),
        ).fetchone()

        if existing:
            print(f"Category group '{existing['name']}' already exists.")
            return

        print(f"Category group: {name}")
        print()

        if not apply:
            try:
                confirm = input("Create category group? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        result = client.create_category_group(name)
        if result:
            log_audit(
                conn,
                "create-category-group",
                "category_group",
                result.get("id"),
                name,
                f"Created category group '{name}'",
                "category create-group",
            )
            conn.commit()
            print(f"Category group '{name}' created successfully.")
            print("Run 'ynab sync' to update local data.")
        else:
            print("Failed to create category group. Check logs.")
    finally:
        conn.close()


def run_create_category(name: str, group: str, apply: bool = False) -> None:
    """Create a new category in YNAB."""
    conn = get_connection()
    try:
        init_db(conn)

        # Resolve category group
        grp = _find_category_group(conn, group)
        if not grp:
            return

        # Check if category already exists
        existing = conn.execute(
            """
            SELECT name FROM budget_categories
            WHERE deleted = 0 AND hidden = 0
              AND LOWER(name) = LOWER(?)
              AND category_group_id = ?
            LIMIT 1
        """,
            (name, grp["id"]),
        ).fetchone()

        if existing:
            print(f"Category '{existing['name']}' already exists in {grp['name']}.")
            return

        # Preview
        print(f"Category: {name}")
        print(f"Group:    {grp['name']}")
        print()

        if not apply:
            try:
                confirm = input("Create category? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        # Create via API
        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        result = client.create_category(grp["id"], name)
        if result:
            log_audit(
                conn,
                "create-category",
                "category",
                result.get("id"),
                name,
                f"Created in group '{grp['name']}'",
                "category create",
            )
            conn.commit()
            print(f"Category '{name}' created successfully.")
            print("Run 'ynab sync' to update local data.")
        else:
            print("Failed to create category. Check logs.")
    finally:
        conn.close()


def _find_category(conn, search: str) -> dict | None:
    """Find a category by name. Tries exact match first, then substring.

    Accepts "Group: Category" or "Category (Group)" to disambiguate when multiple
    categories share the same name.
    """
    month = current_month()
    cat_name, group_filter = parse_category_input(search)

    def _apply_group_filter(rows, group):
        if group is None:
            return rows
        group_lower = strip_emoji_prefix(group).lower()
        return [
            r
            for r in rows
            if strip_emoji_prefix(r["category_group_name"]).lower() == group_lower
            or r["category_group_name"].lower() == group_lower
        ]

    # Try exact match first (case-insensitive)
    exact = conn.execute(
        """
        SELECT id, name, category_group_name, goal_type, goal_target
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND LOWER(name) = LOWER(?)
        ORDER BY name
    """,
        (month, cat_name),
    ).fetchall()

    if exact:
        filtered = _apply_group_filter(exact, group_filter)
        candidates = filtered if filtered else exact
        if len(candidates) == 1:
            return dict(candidates[0])
        # Multiple exact matches - show with group names
        print(f"Multiple categories match '{search}':")
        for r in candidates:
            print(f"  {r['name']}  (group: {r['category_group_name']})")
        print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
        print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
        return None

    # Fall back to substring match
    rows = conn.execute(
        """
        SELECT id, name, category_group_name, goal_type, goal_target
        FROM budget_categories
        WHERE budget_month = ?
          AND deleted = 0 AND hidden = 0
          AND LOWER(name) LIKE LOWER(?)
        ORDER BY name
    """,
        (month, f"%{cat_name}%"),
    ).fetchall()

    if not rows:
        print(f"No category found matching '{search}'")
        return None

    filtered = _apply_group_filter(rows, group_filter)
    candidates = filtered if filtered else rows

    if len(candidates) == 1:
        return dict(candidates[0])

    print(f"Multiple categories match '{search}':")
    for r in candidates:
        print(f"  {r['name']}  (group: {r['category_group_name']})")
    print("\nDisambiguate with 'Group: Category' or 'Category (Group)' format.")
    print("Example: 'Business: Licenses & Fees' or 'Licenses & Fees (Business)'")
    return None


GOAL_TYPE_LABELS = {
    "MF": "Monthly Funding",
    "TB": "Target Balance",
    "TBD": "Target Balance by Date",
    "NEED": "Needed for Spending",
}


def run_set_goal(
    category: str, amount: float, goal_type: str = "MF", by_date: str | None = None, apply: bool = False
) -> None:
    """Set or update a goal on a category."""
    conn = get_connection()
    try:
        init_db(conn)

        cat = _find_category(conn, category)
        if not cat:
            return

        if goal_type == "TBD" and not by_date:
            print("Error: --by-date is required for TBD (Target Balance by Date) goals.")
            return

        target_month = None
        if by_date:
            target_month = f"{by_date}-01" if len(by_date) == 7 else by_date

        # Preview
        label = GOAL_TYPE_LABELS.get(goal_type, goal_type)
        current_goal = cat["goal_type"] or "None"
        current_target = cat["goal_target"] or 0

        print(f"Category:     {cat['name']}")
        print(f"Current goal: {GOAL_TYPE_LABELS.get(current_goal, current_goal)} (${current_target:,.2f})")
        print(f"New goal:     {label} (${amount:,.2f})")
        if target_month:
            print(f"By date:      {target_month[:7]}")
        print()

        if not apply:
            try:
                confirm = input("Set goal? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        amount_mu = round(amount * 1000)
        result = client.update_category_goal(cat["id"], goal_type, amount_mu, target_month)
        if result:
            old_label = GOAL_TYPE_LABELS.get(current_goal, current_goal)
            detail = f"Goal changed: {old_label} (${current_target:,.2f}) → {label} (${amount:,.2f})"
            if target_month:
                detail += f" by {target_month[:7]}"
            log_audit(conn, "set-goal", "goal", cat["id"], cat["name"], detail, "category set-goal")
            conn.commit()
            print(f"Goal set: {label} ${amount:,.2f}")
            print("Run 'ynab sync' to update local data.")
        else:
            print("Failed to set goal. Check logs.")
    finally:
        conn.close()


def run_clear_goal(category: str, apply: bool = False) -> None:
    """Remove the goal from a category."""
    conn = get_connection()
    try:
        init_db(conn)

        cat = _find_category(conn, category)
        if not cat:
            return

        if not cat["goal_type"]:
            print(f"{cat['name']} has no goal to clear.")
            return

        current_goal = cat["goal_type"]
        current_target = cat["goal_target"] or 0
        print(f"Category:     {cat['name']}")
        print(f"Current goal: {GOAL_TYPE_LABELS.get(current_goal, current_goal)} (${current_target:,.2f})")
        print("Action:       Remove goal")
        print()

        if not apply:
            try:
                confirm = input("Clear goal? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        result = client.clear_category_goal(cat["id"])
        if result:
            old_label = GOAL_TYPE_LABELS.get(current_goal, current_goal)
            log_audit(
                conn,
                "clear-goal",
                "goal",
                cat["id"],
                cat["name"],
                f"Goal removed: was {old_label} (${current_target:,.2f})",
                "category clear-goal",
            )
            conn.commit()
            print(f"Goal removed from {cat['name']}.")
            print("Run 'ynab sync' to update local data.")
        else:
            print("Failed to clear goal. Check logs.")
    finally:
        conn.close()
