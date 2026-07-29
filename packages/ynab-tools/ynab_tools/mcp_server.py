"""MCP server exposing ynab-tools commands as callable tools for Claude Code."""

import io
import logging
import os
import sqlite3
import sys
from contextlib import redirect_stdout

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "ynab-tools",
    instructions=(
        "Check category_balance before funding to verify current state. "
        "Negative available balance means overspent; positive means funded. "
        "Never create or modify transactions without explicit user confirmation."
    ),
)

# ── Shared read-only connection for report tools ──

_read_conn: sqlite3.Connection | None = None


def _get_read_conn() -> sqlite3.Connection:
    """Get or create a shared read-only SQLite connection for report tools."""
    global _read_conn
    if _read_conn is None:
        from .config import DB_PATH

        _read_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        _read_conn.row_factory = sqlite3.Row
    return _read_conn


def _capture(func, *args, **kwargs) -> str:
    """Call a function that prints to stdout and return the output as a string."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        func(*args, **kwargs)
    return buf.getvalue()


# ── MCP tool annotations ──
# Spec defaults: destructiveHint=True, openWorldHint=True. Both must be set
# explicitly when the tool is non-destructive or closed-world.
#
# Most ynab-tools read tools query a local SQLite cache (closed world).
# Tools that call the YNAB cloud API (sync, mutations) are open world.
_READ_LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_READ_OPEN = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_WRITE_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
_WRITE_YNAB = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_DESTROY_YNAB = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


# ── Sync ──


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        openWorldHint=True,
    )
)
def sync_data(full: bool = False, months: int = 12) -> str:
    """Sync YNAB budget data to local SQLite database. Run this before other tools if data is stale."""
    global _read_conn
    try:
        from .sync import run_sync

        result = _capture(run_sync, months=months, full=full)
        # Invalidate shared read connection so reports see fresh data
        if _read_conn is not None:
            _read_conn.close()
            _read_conn = None
        return result
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in sync_data")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def sync_status() -> str:
    """Show database sync status - row counts, last sync time, and data freshness."""
    try:
        from .sync import show_status

        return _capture(show_status)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in sync_status")
        return f"Error: {e}"


# ── Budget & Reports ──


@mcp.tool(annotations=_READ_LOCAL)
def budget_check(month: str | None = None, overspend_plan: bool = False) -> str:
    """Budget health: Ready to Assign, overspent, near-limit, underfunded goals, and upcoming planned expenses."""
    try:
        if overspend_plan:
            from .reports.budget import run_overspend_plan

            return _capture(run_overspend_plan, month=month)
        from .reports.budget import run_budget_check

        return _capture(run_budget_check, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in budget_check")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def month_end_report(month: str | None = None) -> str:
    """Consolidated month-end closeout report: income, spending vs budget, overspent categories,
    uncategorized/unapproved counts, pending transactions, surplus categories, and planned expenses.
    """
    try:
        from .reports.month_end import run_month_end

        return _capture(run_month_end, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in month_end_report")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def monthly_summary(months: int = 6) -> str:
    """Monthly income vs spending vs net over time. Shows surplus/deficit status per month and totals."""
    try:
        from .reports.summary import run_summary

        return _capture(run_summary, months=months)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in monthly_summary")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def spending_report(months: int = 1, breakdown: bool = False) -> str:
    """Get spending by category compared to budget targets.

    Shows amount spent, budgeted, and percent used per category.
    """
    try:
        if breakdown:
            from .reports.spending import run_spending_breakdown

            return _capture(run_spending_breakdown, months=months)
        from .reports.spending import run_spending

        return _capture(run_spending, months=months)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in spending_report")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def category_trend(category: str, months: int = 6) -> str:
    """Show spending trend for a specific category over time. Returns monthly amounts and averages."""
    try:
        from .reports.spending import run_category_trend

        return _capture(run_category_trend, category=category, months=months)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in category_trend")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def spending_pace(month: str | None = None) -> str:
    """Mid-month spending pace: which categories are on track, running hot, or underspent.

    Shows budgeted vs spent, % of budget used, pace relative to days elapsed, projected
    month-end spend, and 3-month trailing average for each category with a budget.
    """
    try:
        from .reports.spending import run_spending_pace

        return _capture(run_spending_pace, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in spending_pace")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def recent_transactions(
    limit: int = 25,
    account: str | None = None,
    payee: str | None = None,
    category: str | None = None,
    memo: str | None = None,
    uncleared: bool = False,
    month: str | None = None,
) -> str:
    """Get recent transactions with split transaction detail. Sorted by date descending."""
    try:
        from .reports.transactions import run_recent

        return _capture(
            run_recent,
            limit=limit,
            account=account,
            payee=payee,
            category=category,
            memo=memo,
            uncleared=uncleared,
            month=month,
        )
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in recent_transactions")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def large_expenses(threshold: float = 500, months: int = 6) -> str:
    """Find expenses over a dollar threshold, grouped by month. Useful for spotting unusual spending."""
    try:
        from .reports.transactions import run_large

        return _capture(run_large, threshold=threshold, months=months)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in large_expenses")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def income_report(months: int = 12) -> str:
    """Show income breakdown: regular pay, bonus detection, and year-to-date comparison."""
    try:
        from .reports.income import run_income

        return _capture(run_income, months=months)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in income_report")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def paycheck_forecast(months: int = 12) -> str:
    """Show paycheck forecast: auto-detect income sources, pay frequency, and project upcoming paychecks."""
    try:
        from .reports.paycheck import run_paycheck

        return _capture(run_paycheck, months=months)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in paycheck_forecast")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def debt_status() -> str:
    """Show debt accounts with balances, interest rates, payment history, and payoff estimates."""
    try:
        from .reports.debt import run_debt_status

        return _capture(run_debt_status)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in debt_status")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_LOCAL)
def net_worth(history: int | None = None) -> str:
    """Take a net worth snapshot or show history. Omit history to take a new snapshot."""
    try:
        from .reports.net_worth import run_snapshot

        return _capture(run_snapshot, history=history, detail=history is None)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in net_worth")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def sinking_funds() -> str:
    """Show sinking fund balances, goal progress, and which categories are underfunded."""
    try:
        from .reports.transactions import run_sinking_funds

        return _capture(run_sinking_funds)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in sinking_funds")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def category_balance(category: str) -> str:
    """Look up current balance, budgeted amount, activity, and goal status for a category."""
    try:
        from .reports.budget import run_balance

        return _capture(run_balance, category=category)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in category_balance")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def transfers(days: int = 30) -> str:
    """Show recent account transfers (e.g. checking to savings)."""
    try:
        from .reports.transactions import run_transfers

        return _capture(run_transfers, days=days)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in transfers")
        return f"Error: {e}"


# ── Funding ──


@mcp.tool(annotations=_READ_LOCAL)
def funding_status(month: str | None = None) -> str:
    """Show funding analysis per category: target vs average spend vs recommendation."""
    try:
        from .reports.funding import run_fund_status

        return _capture(run_fund_status, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in funding_status")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def fund_goals_preview(month: str | None = None) -> str:
    """Dry-run: show what would change if all underfunded goals were funded. Use fund_goals_apply to execute."""
    try:
        from .reports.funding import run_fund_goals

        return _capture(run_fund_goals, month=month, apply=False)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in fund_goals_preview")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def fund_goals_apply(month: str | None = None) -> str:
    """Fund all underfunded goal categories. WRITES TO YNAB. Run fund_goals_preview first to review."""
    try:
        from .reports.funding import run_fund_goals

        return _capture(run_fund_goals, month=month, apply=True)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in fund_goals_apply")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def fund_category(category: str, amount: str, month: str | None = None) -> str:
    """Add to or set the budgeted amount for a category. WRITES TO YNAB.

    Amount formats:
      "100" or "+100" - add $100 to existing budget (default for overspend coverage)
      "-50"           - subtract $50 from existing budget
      "=600"          - set budget to exactly $600 (use for explicit absolute targets)
    """
    try:
        from .reports.funding import run_fund

        try:
            float(amount.lstrip("+"))
        except ValueError:
            return f"Error: invalid amount '{amount}'; expected a number like 850 or +100"
        return _capture(run_fund, category=category, amount_str=amount, month=month, apply=True)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in fund_category")
        return f"Error: {e}"


# ── Transactions ──


@mcp.tool(annotations=_WRITE_YNAB)
def add_transaction(
    account: str,
    amount: float,
    payee: str,
    category: str | None = None,
    date: str | None = None,
    memo: str | None = None,
    inflow: bool = False,
) -> str:
    """Create a new transaction in YNAB. WRITES TO YNAB. Shows preview then pushes to API."""
    try:
        from .reports.transactions import run_add

        return _capture(
            run_add,
            account=account,
            amount=amount,
            payee=payee,
            category=category,
            date=date,
            memo=memo,
            inflow=inflow,
            apply=True,
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in add_transaction")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def update_transaction(
    transaction_id: str,
    category: str | None = None,
    memo: str | None = None,
    splits: list[dict] | None = None,
) -> str:
    """Update an existing transaction's category, memo, or split it across categories. WRITES TO YNAB.

    To change category or memo, pass category and/or memo.
    To clear the category (uncategorize), pass category="uncategorized" or category="".
    To split a transaction, pass splits as a list of {"category": str, "amount": float} dicts
    where amounts are positive dollar values (e.g. 129.30) that must sum to the transaction total.
    You cannot use both category and splits at the same time.

    Example split: splits=[{"category": "Home: Decor", "amount": 129.30},
                            {"category": "Home: Household Supplies", "amount": 44.61}]
    """
    try:
        from .reports.transactions import run_update

        return _capture(
            run_update,
            transaction_id=transaction_id,
            category=category,
            memo=memo,
            splits=splits,
            apply=True,
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in update_transaction")
        return f"Error: {e}"


@mcp.tool(annotations=_DESTROY_YNAB)
def delete_transaction(transaction_id: str) -> str:
    """Delete a transaction from YNAB. WRITES TO YNAB. This cannot be undone."""
    try:
        from .reports.transactions import run_delete

        return _capture(run_delete, transaction_id=transaction_id, apply=True)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in delete_transaction")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def categorize_transactions(apply: bool = False) -> str:
    """Find uncategorized transactions and suggest categories based on payee patterns."""
    try:
        from .categories.classifier import run_categorize

        return _capture(run_categorize, apply=apply)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in categorize_transactions")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_OPEN)
def unapproved_transactions() -> str:
    """List unapproved transactions needing review - separated into 'needs category' and 'ready to approve'."""
    try:
        from .reports.transactions import run_unapproved

        return _capture(run_unapproved)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in unapproved_transactions")
        return f"Error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def approve_transactions(
    transaction_id: str | None = None,
    all_categorized: bool = False,
) -> str:
    """Approve unapproved transactions in YNAB. WRITES TO YNAB.

    Either approve a single transaction by ID, or approve all categorized transactions at once.
    """
    try:
        from .reports.transactions import run_approve

        return _capture(
            run_approve,
            transaction_id=transaction_id,
            all_categorized=all_categorized,
            apply=True,
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in approve_transactions")
        return f"Error: {e}"


# ── Planned Expenses ──


@mcp.tool(annotations=_READ_LOCAL)
def planned_expenses() -> str:
    """List all active planned expenses with funding gaps and due dates."""
    try:
        from .reports.planned import run_plan_list

        return _capture(run_plan_list)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in planned_expenses")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_LOCAL)
def add_planned_expense(category: str, amount: float, by_date: str, memo: str | None = None) -> str:
    """Add a planned expense to track. Stored locally - surfaces in budget_check when due within 30 days."""
    try:
        from .reports.planned import run_plan_add

        return _capture(run_plan_add, category=category, amount=amount, by_date=by_date, memo=memo)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in add_planned_expense")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_LOCAL)
def complete_planned_expense(plan_id: int) -> str:
    """Mark a planned expense as completed."""
    try:
        from .reports.planned import run_plan_done

        return _capture(run_plan_done, plan_id=plan_id)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in complete_planned_expense")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_LOCAL)
def edit_planned_expense(
    plan_id: int,
    amount: float | None = None,
    by_date: str | None = None,
    memo: str | None = None,
    category: str | None = None,
) -> str:
    """Edit an existing planned expense. Only provided fields are updated."""
    try:
        from .reports.planned import run_plan_edit

        return _capture(
            run_plan_edit,
            plan_id=plan_id,
            amount=amount,
            by_date=by_date,
            memo=memo,
            category=category,
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in edit_planned_expense")
        return f"Error: {e}"


# ── Payee Management ──


@mcp.tool(annotations=_READ_LOCAL)
def payee_audit() -> str:
    """Find payee name mismatches between bank imports and YNAB payee names. Read-only analysis."""
    try:
        from .config import require_credentials
        from .db import get_connection
        from .payees.audit import cmd_audit

        require_credentials()
        conn = get_connection()
        try:
            result = _capture(cmd_audit, conn)
        finally:
            conn.close()
        return result
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except SystemExit:
        return "Error: YNAB credentials not configured. Run 'ynab configure'."
    except Exception as e:
        logger.exception("Unexpected error in payee_audit")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def payee_preview() -> str:
    """Preview what payee fixes the current rules would apply (dry-run). Use payee_fix to execute."""
    try:
        from .config import require_credentials
        from .db import get_connection
        from .payees.audit import cmd_preview

        require_credentials()
        conn = get_connection()
        try:
            result = _capture(cmd_preview, conn)
        finally:
            conn.close()
        return result
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except SystemExit:
        return "Error: YNAB credentials not configured. Run 'ynab configure'."
    except Exception as e:
        logger.exception("Unexpected error in payee_preview")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def payee_fix() -> str:
    """Apply payee rule fixes to YNAB transactions. WRITES TO YNAB. Run payee_preview first to see what will change."""
    try:
        from .client import YNABClient
        from .config import require_credentials
        from .db import get_connection
        from .payees.audit import cmd_fix

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)
        conn = get_connection()
        try:
            result = _capture(cmd_fix, conn, client)
        finally:
            conn.close()
        return result
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except SystemExit:
        return "Error: YNAB credentials not configured. Run 'ynab configure'."
    except Exception as e:
        logger.exception("Unexpected error in payee_fix")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def create_payee(name: str) -> str:
    """Create a new payee in YNAB. WRITES TO YNAB."""
    try:
        from .client import YNABClient
        from .config import require_credentials

        token, plan_id = require_credentials()
        client = YNABClient(token, plan_id)
        result = client.create_payee(name)
        if result:
            return f"Payee '{name}' created (ID: {result['id']}). Run sync_data to update local data."
        return f"Failed to create payee '{name}'. Check logs."
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    except SystemExit:
        return "Error: YNAB credentials not configured. Run 'ynab configure'."
    except Exception as e:
        logger.exception("Unexpected error in create_payee")
        return f"Error: {e}"


# ── Category Management ──


@mcp.tool(annotations=_WRITE_YNAB)
def create_category_group(name: str) -> str:
    """Create a new category group in YNAB. WRITES TO YNAB."""
    try:
        from .reports.categories import run_create_group

        return _capture(run_create_group, name=name, apply=True)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in create_category_group")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def create_category(name: str, group: str) -> str:
    """Create a new budget category in YNAB. WRITES TO YNAB."""
    try:
        from .reports.categories import run_create_category

        return _capture(run_create_category, name=name, group=group, apply=True)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in create_category")
        return f"Error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def set_category_goal(
    category: str,
    amount: float,
    goal_type: str = "MF",
    by_date: str | None = None,
) -> str:
    """Set or update a goal on a YNAB category. WRITES TO YNAB."""
    try:
        from .reports.categories import run_set_goal

        return _capture(
            run_set_goal, category=category, amount=amount, goal_type=goal_type, by_date=by_date, apply=True
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in set_category_goal")
        return f"Error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def clear_category_goal(category: str) -> str:
    """Remove the goal from a YNAB category. WRITES TO YNAB."""
    try:
        from .reports.categories import run_clear_goal

        return _capture(run_clear_goal, category=category, apply=True)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in clear_category_goal")
        return f"Error: {e}"


# ── Paycheck Funding ──


@mcp.tool(annotations=_READ_LOCAL)
def paycheck_funding(month: str | None = None) -> str:
    """Generate a prioritized funding plan based on available RTA. Shows what to fund and in what order.

    Tiers: 1=Cover overspent, 2=Due soon (14 days), 3=Bridge daily spending,
    4=Monthly bills (goal categories), 5=Remaining goals.
    Use paycheck_funding_apply to execute the plan.
    """
    try:
        from .reports.paycheck_funding import run_paycheck_funding

        return _capture(run_paycheck_funding, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in paycheck_funding")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def paycheck_funding_apply(month: str | None = None, through_tier: int = 2) -> str:
    """Execute the paycheck funding plan through a specified tier. WRITES TO YNAB.

    Funds categories in priority order, stopping when RTA is exhausted or tier limit reached.
    All changes are logged to funding_log with source='paycheck-funding'.
    Run paycheck_funding first to preview the plan.
    """
    try:
        from .reports.paycheck_funding import run_paycheck_funding_apply

        return _capture(run_paycheck_funding_apply, month=month, through_tier=through_tier)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in paycheck_funding_apply")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def paycheck_breakdown(month: str | None = None) -> str:
    """Show budget split: regular-paycheck-funded vs bonus-funded categories.

    HOT = 3mo actual average exceeds budget target by more than 5%.
    Configure bonus-funded categories via YNAB_BONUS_FUNDED_GROUPS and
    YNAB_BONUS_FUNDED_CATEGORIES in ~/.config/ynab-tools/config.json.
    """
    try:
        from .reports.paycheck_breakdown import run_paycheck_breakdown

        return _capture(run_paycheck_breakdown, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in paycheck_breakdown")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def two_pot_compliance(months: int = 3, month: str | None = None) -> str:
    """Show two-pot structural compliance for recent bonus months.

    The primary metric is structural_backwards: sum(budgeted for regular-pot categories)
    minus (regular_pay x checks). When TBB = 0 this is exact; otherwise it is a lower bound.

    Also shows Holding: Next Month delta per bonus month (negative delta is a warning signal),
    and workflow_gap from money_movements (all budget moves including YNAB app moves, may double-count top-ups).

    Args:
        months: Number of recent bonus months to show (default 3).
        month: Target a specific month in YYYY-MM format (e.g. "2026-04"). When provided,
               only that month is shown regardless of the months parameter.

    Requires YNAB_REGULAR_PAY and YNAB_BONUS_THRESHOLD to be configured.
    """
    try:
        from .reports.two_pot_report import run_two_pot_report

        return _capture(run_two_pot_report, months=months, month=month)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in two_pot_compliance")
        return f"Error: {e}"


# ── Reconciliation ──


@mcp.tool(annotations=_READ_LOCAL)
def reconcile_list() -> str:
    """Show reconciliation status for all accounts - YNAB balance, cleared balance, and last reconciled date."""
    try:
        from .reports.reconcile import run_reconcile_list

        return _capture(run_reconcile_list)
    except (sqlite3.Error, OSError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in reconcile_list")
        return f"Error: {e}"


@mcp.tool(annotations=_WRITE_YNAB)
def reconcile_account(account: str, balance: float, apply: bool = False) -> str:
    """Compare account to real-world balance; optionally create an adjustment. Set apply=true to WRITE."""
    try:
        from .reports.reconcile import run_reconcile

        return _capture(run_reconcile, account=account, balance=balance, apply=apply)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in reconcile_account")
        return f"Error: {e}"


@mcp.tool(annotations=_READ_LOCAL)
def get_subscriptions() -> str:
    """List recurring subscription charges with frequency, cost, and status."""
    try:
        from .reports.subscriptions import run_subscriptions

        return _capture(run_subscriptions)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in get_subscriptions")
        return f"Error: {e}"


# ── Money Movements ──


@mcp.tool(annotations=_READ_LOCAL)
def movement_log(limit: int = 50, month: str | None = None, category: str | None = None) -> str:
    """Show budget funding movements from the YNAB money_movements API.

    Returns all money moved between categories - captures both ynab-tools CLI moves
    and direct YNAB app moves. Filter by month (YYYY-MM) or partial category name.
    """
    try:
        from .db import get_connection

        conn = get_connection()
        try:
            params: list = []
            where_clauses = ["deleted = 0"]

            if month:
                month_str = month[:7] + "-01" if len(month) >= 7 else month
                where_clauses.append("month = ?")
                params.append(month_str)

            if category:
                where_clauses.append(
                    "(LOWER(from_category_name) LIKE LOWER(?) OR LOWER(to_category_name) LIKE LOWER(?))"
                )
                params.extend([f"%{category}%", f"%{category}%"])

            where_sql = " AND ".join(where_clauses)
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT moved_at, from_category_name, to_category_name, amount, month
                FROM money_movements
                WHERE {where_sql}
                ORDER BY moved_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

            if not rows:
                return "No budget moves found matching the given filters."

            lines = [f"{'Moved At':<28} {'From':<30} {'To':<30} {'Amount':>10}"]
            lines.append("-" * 102)
            for r in rows:
                from_name = r["from_category_name"] or "RTA"
                to_name = r["to_category_name"] or "RTA"
                moved_at = (r["moved_at"] or "")[:19]
                lines.append(f"{moved_at:<28} {from_name:<30} {to_name:<30} ${r['amount']:>9,.2f}")

            lines.append(f"\n{len(rows)} moves shown")
            return "\n".join(lines)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.exception("Unexpected error in movement_log")
        return f"Error: {e}"


# ── Entry point ──


def main():
    """Run the MCP server."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
