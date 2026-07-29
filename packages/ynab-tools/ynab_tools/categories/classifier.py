"""Category suggestion engine for uncategorized YNAB transactions."""

import json
import logging
import re
import sqlite3

from ..client import YNABClient
from ..config import CATEGORY_DEFS_FILE, require_credentials
from ..db import get_connection, init_db

logger = logging.getLogger(__name__)

# Confidence levels
HIGH = "HIGH"  # typical_payee exact match - safe to auto-apply
MEDIUM = "MEDIUM"  # keyword or historical pattern match
LOW = "LOW"  # disambiguation rule or weak signal

# Minimum split occurrences to consider a category in suggestions
MIN_SPLIT_COUNT = 3
# Minimum proportion of splits a category must represent to be suggested
MIN_SPLIT_PROPORTION = 0.05


def load_category_definitions() -> dict:
    """Load category definitions from JSON."""
    if not CATEGORY_DEFS_FILE.exists():
        logger.error("Category definitions not found: %s", CATEGORY_DEFS_FILE)
        return {}
    try:
        with open(CATEGORY_DEFS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load category definitions: %s", e)
        return {}


def get_uncategorized_transactions(conn: sqlite3.Connection) -> list[dict]:
    """Find transactions without a category (excluding transfers and splits)."""
    rows = conn.execute("""
        SELECT t.id, t.date, t.amount, t.payee_name, t.memo, t.account_name, t.approved
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0
          AND a.on_budget = 1
          AND (t.category_name IS NULL OR t.category_name = '' OR t.category_name = 'Uncategorized')
          AND t.transfer_account_id IS NULL
          AND t.payee_name != 'Split (Multiple Categories...)'
          AND t.payee_name NOT LIKE 'Transfer%'
          AND NOT EXISTS (
              SELECT 1 FROM subtransactions
              WHERE transaction_id = t.id
                AND deleted = 0
          )
        ORDER BY t.date DESC
    """).fetchall()

    return [
        {
            "id": row["id"],
            "date": row["date"],
            "amount": row["amount"] or 0,
            "payee_name": row["payee_name"] or "",
            "memo": row["memo"] or "",
            "account_name": row["account_name"] or "",
            "approved": row["approved"],
        }
        for row in rows
    ]


def get_historical_categories(conn: sqlite3.Connection, payee_name: str) -> list[tuple[str, int]]:
    """Get category usage history for a payee, ordered by frequency."""
    rows = conn.execute(
        """
        SELECT COALESCE(st.category_name, t.category_name) AS cat, COUNT(*) AS cnt
        FROM transactions t
        LEFT JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
        LEFT JOIN payees p ON t.payee_id = p.id
        WHERE t.deleted = 0
          AND COALESCE(t.payee_name, p.name) = ?
          AND COALESCE(st.category_name, t.category_name) IS NOT NULL
          AND COALESCE(st.category_name, t.category_name) != ''
          AND COALESCE(st.category_name, t.category_name) != 'Uncategorized'
          AND COALESCE(st.category_name, t.category_name) != 'Split (Multiple Categories...)'
        GROUP BY cat
        ORDER BY cnt DESC
    """,
        (payee_name,),
    ).fetchall()

    return [(row["cat"], row["cnt"]) for row in rows]


def get_category_id_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Build category_name -> category_id map from the most recent budget month."""
    rows = conn.execute("""
        SELECT id, name FROM budget_categories
        WHERE budget_month = (SELECT MAX(budget_month) FROM budget_categories)
          AND deleted = 0 AND hidden = 0
    """).fetchall()
    return {row["name"]: row["id"] for row in rows}


def classify_transaction(txn: dict, defs: dict, conn: sqlite3.Connection) -> dict | None:
    """
    Score a transaction against category definitions and history.

    Returns: {"category": str, "confidence": HIGH|MEDIUM|LOW, "reason": str}
    or None if no match.
    """
    payee = txn["payee_name"]
    memo = txn["memo"].lower()
    payee_lower = payee.lower()
    categories = defs.get("categories", {})
    disambiguation = defs.get("disambiguation_rules", [])

    # 1. Typical payee match (HIGH confidence)
    for cat_name, cat_def in categories.items():
        for tp in cat_def.get("typical_payees", []):
            if tp.lower() == payee_lower or payee_lower.startswith(tp.lower()):
                return {"category": cat_name, "confidence": HIGH, "reason": f"typical payee match: {tp}"}

    # 2. Keyword match in memo (MEDIUM confidence) - word boundaries for short keywords
    for cat_name, cat_def in categories.items():
        for kw in cat_def.get("keywords", []):
            kw_lower = kw.lower()
            if len(kw_lower) <= 4:
                matched = bool(re.search(r"\b" + re.escape(kw_lower) + r"\b", memo))
            else:
                matched = kw_lower in memo
            if matched:
                return {"category": cat_name, "confidence": MEDIUM, "reason": f"memo keyword: '{kw}'"}

    # 3. Historical pattern (MEDIUM confidence) - if payee consistently goes to one category
    history = get_historical_categories(conn, payee)
    if history:
        top_cat, top_count = history[0]
        total = sum(c for _, c in history)
        if len(history) == 1 and top_count >= 2:
            return {
                "category": top_cat,
                "confidence": MEDIUM,
                "reason": f"historical: {top_count}/{total} transactions",
            }
        elif top_count >= 3 and top_count / total >= 0.8:
            return {
                "category": top_cat,
                "confidence": MEDIUM,
                "reason": f"historical: {top_count}/{total} ({top_count / total:.0%})",
            }

    # 4. Disambiguation rules (LOW confidence)
    for rule in disambiguation:
        rule_payee = rule.get("payee", "")
        if rule_payee == "*" or payee_lower.startswith(rule_payee.lower()):
            # Only use if we can extract a default from the rule text
            rule_text = rule.get("rule", "").lower()
            if "default is" in rule_text or "default" in rule_text:
                for cat_name in categories:
                    if cat_name.lower() in rule_text:
                        return {
                            "category": cat_name,
                            "confidence": LOW,
                            "reason": f"disambiguation: {rule['rule'][:60]}",
                        }

    return None


def get_split_payees(defs: dict) -> set[str]:
    """Get the set of payees known to produce split transactions."""
    return {p.lower() for p in defs.get("split_payees", [])}


def get_split_candidates(conn: sqlite3.Connection, split_payees: set[str]) -> list[dict]:
    """Find uncategorized transactions from known multi-category payees."""
    rows = conn.execute("""
        SELECT t.id, t.date, t.amount, t.payee_name, t.memo, t.account_name, t.approved
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.deleted = 0
          AND a.on_budget = 1
          AND (t.category_name IS NULL OR t.category_name = '' OR t.category_name = 'Uncategorized')
          AND t.transfer_account_id IS NULL
          AND t.payee_name != 'Split (Multiple Categories...)'
          AND t.payee_name NOT LIKE 'Transfer%'
          AND NOT EXISTS (
              SELECT 1 FROM subtransactions
              WHERE transaction_id = t.id
                AND deleted = 0
          )
        ORDER BY t.date DESC
    """).fetchall()

    candidates = []
    for row in rows:
        payee = row["payee_name"] or ""
        if payee.lower() in split_payees:
            candidates.append(
                {
                    "id": row["id"],
                    "date": row["date"],
                    "amount": row["amount"] or 0,
                    "payee_name": payee,
                    "memo": row["memo"] or "",
                    "account_name": row["account_name"] or "",
                    "approved": row["approved"],
                }
            )
    return candidates


def get_split_history(conn: sqlite3.Connection, payee_name: str) -> list[dict]:
    """
    Analyze historical split patterns for a payee.

    Returns a list of {"category": str, "count": int, "proportion": float,
    "avg_amount": float} sorted by frequency, filtered to significant categories.
    """
    rows = conn.execute(
        """
        SELECT st.category_name AS cat,
               COUNT(*) AS cnt,
               ROUND(AVG(ABS(st.amount)), 2) AS avg_amt
        FROM transactions t
        JOIN subtransactions st ON st.transaction_id = t.id AND st.deleted = 0
        LEFT JOIN payees p ON t.payee_id = p.id
        WHERE t.deleted = 0
          AND COALESCE(t.payee_name, p.name) = ?
          AND st.category_name IS NOT NULL
          AND st.category_name != ''
          AND st.category_name != 'Uncategorized'
        GROUP BY st.category_name
        ORDER BY cnt DESC
    """,
        (payee_name,),
    ).fetchall()

    if not rows:
        return []

    total = sum(row["cnt"] for row in rows)
    results = []
    for row in rows:
        proportion = row["cnt"] / total
        if row["cnt"] >= MIN_SPLIT_COUNT and proportion >= MIN_SPLIT_PROPORTION:
            results.append(
                {
                    "category": row["cat"],
                    "count": row["cnt"],
                    "proportion": proportion,
                    "avg_amount": row["avg_amt"],
                }
            )

    return results


def suggest_split(txn: dict, history: list[dict], cat_id_map: dict[str, str]) -> list[dict] | None:
    """
    Generate a proportional split suggestion for a transaction based on
    historical category distribution.

    Returns a list of {"category": str, "category_id": str, "amount": float,
    "proportion": float} or None if insufficient history.
    """
    if not history:
        return None

    # Filter to categories that still exist in the budget
    valid = [h for h in history if h["category"] in cat_id_map]
    if not valid:
        return None

    # Renormalize proportions after filtering
    total_prop = sum(h["proportion"] for h in valid)
    if total_prop == 0:
        return None

    txn_amount = txn["amount"]  # Already in dollars, negative for outflows
    suggestions = []
    allocated = 0.0

    for i, h in enumerate(valid):
        norm_prop = h["proportion"] / total_prop
        if i == len(valid) - 1:
            # Last item gets the remainder to avoid rounding issues
            amount = round(txn_amount - allocated, 2)
        else:
            amount = round(txn_amount * norm_prop, 2)
            allocated += amount

        suggestions.append(
            {
                "category": h["category"],
                "category_id": cat_id_map[h["category"]],
                "amount": amount,
                "proportion": norm_prop,
            }
        )

    return suggestions


def dollars_to_milliunits(dollars: float) -> int:
    """Convert dollar amount to YNAB milliunits."""
    return round(dollars * 1000)


def run_split(index: int | None = None, apply: bool = False) -> None:
    """List split candidates, show suggestions, and optionally apply."""
    conn = get_connection()
    try:
        init_db(conn)
        defs = load_category_definitions()
        if not defs:
            return

        split_payees = get_split_payees(defs)
        if not split_payees:
            print("No split_payees defined in category_definitions.json")
            return

        candidates = get_split_candidates(conn, split_payees)
        if not candidates:
            print("No split candidates found!")
            return

        cat_id_map = get_category_id_map(conn)

        if index is None:
            # List mode: show all candidates
            print(f"Split Candidates ({len(candidates)} transactions)")
            print("=" * 80)
            for i, txn in enumerate(candidates, 1):
                print(
                    f"  {i:>3}. {txn['date']}  {txn['payee_name']:<25} "
                    f"${abs(txn['amount']):>10,.2f}  ({txn['account_name']})"
                )
            print()
            print("Usage: ynab split <number>          - show suggested split")
            print("       ynab split <number> --apply  - apply suggested split")
            return

        # Detail/apply mode for a specific candidate
        if index < 1 or index > len(candidates):
            print(f"Invalid index {index}. Valid range: 1-{len(candidates)}")
            return

        txn = candidates[index - 1]
        history = get_split_history(conn, txn["payee_name"])
        suggestion = suggest_split(txn, history, cat_id_map)

        print(f"Transaction #{index}")
        print(f"  Date:    {txn['date']}")
        print(f"  Payee:   {txn['payee_name']}")
        print(f"  Amount:  ${abs(txn['amount']):,.2f}")
        print(f"  Account: {txn['account_name']}")
        if txn["memo"]:
            print(f"  Memo:    {txn['memo']}")
        print()

        if not suggestion:
            print("  No split suggestion available - insufficient history for this payee.")
            print("  Categorize this transaction in YNAB directly.")
            return

        print("Suggested Split (based on historical patterns):")
        print(f"  {'Category':<35} {'Amount':>10}  {'Share':>6}")
        print(f"  {'-' * 35} {'-' * 10}  {'-' * 6}")
        check_total = 0.0
        for s in suggestion:
            check_total += s["amount"]
            print(f"  {s['category']:<35} ${abs(s['amount']):>9,.2f}  {s['proportion']:>5.0%}")
        print(f"  {'-' * 35} {'-' * 10}")
        print(f"  {'Total':<35} ${abs(check_total):>9,.2f}")
        print()

        if not apply:
            print("This was a preview. To apply this split:")
            print(f"  ynab split {index} --apply")
            print()
            print("WARNING: Once applied, splits cannot be modified via the API.")
            print("         Corrections must be made in the YNAB app.")
            return

        # Build subtransactions payload
        subtxns = []
        for s in suggestion:
            subtxns.append(
                {
                    "amount": dollars_to_milliunits(s["amount"]),
                    "category_id": s["category_id"],
                }
            )

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        print(f"Applying split to transaction {txn['id'][:8]}...")
        success = client.split_transaction(txn["id"], subtxns)

        if success:
            print("  Split applied successfully!")
            print("  Run 'ynab sync' to update local data.")
        else:
            print("  Failed to apply split. Check logs for details.")
    finally:
        conn.close()


def run_categorize(apply: bool = False) -> None:
    """Main entry point: find uncategorized transactions and suggest/apply categories."""
    conn = get_connection()
    try:
        init_db(conn)

        defs = load_category_definitions()
        if not defs:
            return

        uncategorized = get_uncategorized_transactions(conn)

        if not uncategorized:
            print("No uncategorized transactions found!")
            return

        print(f"Found {len(uncategorized)} uncategorized transactions\n")

        suggestions = []
        for txn in uncategorized:
            result = classify_transaction(txn, defs, conn)
            if result:
                suggestions.append({**txn, **result})

        if not suggestions:
            print("No category suggestions could be determined.")
            print("Consider adding more payee/keyword definitions to category_definitions.json")
            return

        # Group by confidence
        by_confidence = {HIGH: [], MEDIUM: [], LOW: []}
        for s in suggestions:
            by_confidence[s["confidence"]].append(s)

        print(f"Suggestions: {len(suggestions)} of {len(uncategorized)} transactions")
        print(f"  HIGH confidence:   {len(by_confidence[HIGH])}")
        print(f"  MEDIUM confidence: {len(by_confidence[MEDIUM])}")
        print(f"  LOW confidence:    {len(by_confidence[LOW])}")
        print()

        # Show suggestions grouped by category
        by_category: dict[str, list] = {}
        for s in suggestions:
            by_category.setdefault(s["category"], []).append(s)

        for cat_name, txns in sorted(by_category.items()):
            total = sum(abs(t["amount"]) for t in txns)
            print(f"## {cat_name} ({len(txns)} txns, ${total:,.2f})")
            for t in txns[:5]:
                conf_marker = {"HIGH": "+", "MEDIUM": "~", "LOW": "?"}[t["confidence"]]
                print(f"  [{conf_marker}] {t['date']} {t['payee_name']:<30} ${t['amount']:>10,.2f}  ({t['reason']})")
            if len(txns) > 5:
                print(f"  ... and {len(txns) - 5} more")
            print()

        # Show split candidates
        split_payees = get_split_payees(defs)
        if split_payees:
            split_candidates = get_split_candidates(conn, split_payees)
            # Exclude transactions we already have single-category suggestions for
            suggested_ids = {s["id"] for s in suggestions}
            split_only = [c for c in split_candidates if c["id"] not in suggested_ids]
            if split_only:
                print(f"SPLIT CANDIDATES ({len(split_only)} transactions from multi-category payees):")
                for c in split_only[:10]:
                    print(f"  {c['date']}  {c['payee_name']:<25} ${abs(c['amount']):>10,.2f}")
                if len(split_only) > 10:
                    print(f"  ... and {len(split_only) - 10} more")
                print("\nUse 'ynab split' to review and apply split suggestions.\n")

        if not apply:
            high_count = len(by_confidence[HIGH])
            print(f"This was a preview. To apply {high_count} HIGH-confidence suggestions:")
            print("  ynab categorize --apply")
            return

        # Only apply HIGH confidence
        to_apply = by_confidence[HIGH]
        if not to_apply:
            print("No HIGH-confidence suggestions to apply.")
            return

        cat_id_map = get_category_id_map(conn)
        updates = []
        skipped = []

        for s in to_apply:
            cat_id = cat_id_map.get(s["category"])
            if cat_id:
                updates.append({"id": s["id"], "category_id": cat_id})
            else:
                skipped.append(s)

        if skipped:
            print(f"Warning: Skipping {len(skipped)} transactions (category ID not found)")
            for s in skipped:
                print(f"  - {s['category']}: {s['payee_name']}")

        if not updates:
            print("No updates to apply.")
            return

        print(f"\nApplying {len(updates)} HIGH-confidence categorizations...")

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)

        # Bulk update in batches of 1000
        success_total = 0
        failed_total = 0
        for i in range(0, len(updates), 1000):
            batch = updates[i : i + 1000]
            result = client.bulk_update_transactions(batch)
            success_total += result["success"]
            failed_total += result["failed"]

        print(f"  Success: {success_total}, Failed: {failed_total}")
        print("\nDone! Run 'ynab sync' to update local data.")
    finally:
        conn.close()
