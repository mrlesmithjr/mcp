"""Single entry point: ynab <command>."""

import argparse
import getpass
import json
import logging
import os
import shutil
import subprocess
import sys


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        prog="ynab",
        description="YNAB budget data sync, payee management, and analysis",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # ── sync ──
    sync_p = sub.add_parser("sync", help="Sync YNAB data to local SQLite")
    sync_p.add_argument("--status", action="store_true", help="Show sync status only")
    sync_p.add_argument("--full", action="store_true", help="Force full sync (ignore delta state)")
    sync_p.add_argument("--months", type=int, default=12, help="Months of history (default: 12)")

    # ── payee ──
    payee_p = sub.add_parser("payee", help="Payee management tools")
    payee_sub = payee_p.add_subparsers(dest="payee_command", help="Payee subcommand")

    payee_sub.add_parser("audit", help="Find all payee mismatches")
    payee_sub.add_parser("preview", help="Preview fixes (dry-run)")
    payee_sub.add_parser("fix", help="Apply fixes (with confirmation)")
    payee_sub.add_parser("validate", help="Validate rules use canonical names")
    payee_sub.add_parser("plans", help="List YNAB plans")

    norm_p = payee_sub.add_parser("normalize", help="Find/fix duplicate payee names")
    norm_p.add_argument("--apply", action="store_true", help="Apply changes")

    orphan_p = payee_sub.add_parser("orphaned", help="Find payees with no transactions")
    orphan_p.add_argument("--api", action="store_true", help="Use API instead of local DB")

    payee_create_p = payee_sub.add_parser("create", help="Create a new payee")
    payee_create_p.add_argument("name", help="Payee name")
    payee_create_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    payee_sub.add_parser("backups", help="List available backup files")

    restore_p = payee_sub.add_parser("restore", help="Restore from backup file")
    restore_p.add_argument("file", help="Backup filename")

    # ── categorize ──
    cat_p = sub.add_parser("categorize", help="Suggest categories for uncategorized transactions")
    cat_p.add_argument("--apply", action="store_true", help="Push high-confidence suggestions to YNAB")

    # ── split ──
    split_p = sub.add_parser("split", help="Split transactions across multiple categories")
    split_p.add_argument("index", nargs="?", type=int, help="Candidate number to review/apply")
    split_p.add_argument("--apply", action="store_true", help="Apply the suggested split")

    # ── net-worth ──
    nw_p = sub.add_parser("net-worth", help="Net worth snapshots")
    nw_p.add_argument(
        "--history", nargs="?", const=12, type=int, metavar="MONTHS", help="Show history (default: 12 entries)"
    )
    nw_p.add_argument("--detail", action="store_true", help="Show latest snapshot with account breakdown")

    # ── budget ──
    budget_p = sub.add_parser("budget", help="Budget check: RTA, overspent, near-limit categories")
    budget_p.add_argument(
        "--cc-audit",
        action="store_true",
        dest="cc_audit",
        help="Audit credit card payment categories vs account balances",
    )
    budget_p.add_argument(
        "--month",
        help="Month to check (YYYY-MM format, default: current month)",
    )
    budget_p.add_argument(
        "--overspend-plan",
        action="store_true",
        dest="overspend_plan",
        help="Classify overspent categories to help determine coverage sources",
    )

    # ── balance ──
    bal_p = sub.add_parser("balance", help="Look up a category balance")
    bal_p.add_argument("category", help="Category name (partial match)")

    # ── transfers ──
    xfer_p = sub.add_parser("transfers", help="Recent account transfers")
    xfer_p.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")

    # ── summary ──
    summary_p = sub.add_parser("summary", help="Monthly income vs spending vs net over time")
    summary_p.add_argument("--months", type=int, default=6, help="Months of history (default: 6)")

    # ── spending ──
    # spending accepts an optional positional subcommand ("breakdown") to avoid
    # conflating the two fundamentally different reports behind a single flag.
    # Subcommand detection happens at dispatch time via args.subcommand.
    spend_p = sub.add_parser(
        "spending",
        help="Current month spending by category vs budget",
        description=(
            "Show spending by category vs budget targets, or use a subcommand:\n"
            "  ynab spending              -- current month category report\n"
            "  ynab spending --months N   -- N-month spending history\n"
            "  ynab spending breakdown    -- fixed vs discretionary breakdown\n"
            "\n"
            "Note: --breakdown is deprecated. Use: ynab spending breakdown"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    spend_p.add_argument(
        "subcommand",
        nargs="?",
        help="Subcommand: 'breakdown' for fixed vs discretionary report",
    )
    spend_p.add_argument("--months", type=int, default=1, help="Months of history (default: 1)")
    spend_p.add_argument(
        "--breakdown",
        action="store_true",
        help="[DEPRECATED] Use: ynab spending breakdown",
    )

    # ── spending-pace ──
    pace_p = sub.add_parser(
        "spending-pace",
        help="Mid-month spending pace: on track, running hot, or underspent by category",
    )
    pace_p.add_argument("--month", help="Target budget month (YYYY-MM, default: current)")

    # ── trends ──
    trend_p = sub.add_parser("trends", help="Category spending trend over time")
    trend_p.add_argument("category", help="Category name (partial match)")
    trend_p.add_argument("--months", type=int, default=6, help="Months of history (default: 6)")

    # ── debt ──
    sub.add_parser("debt", help="Debt accounts, balances, and payoff estimates")

    # ── subscriptions ──
    sub.add_parser("subscriptions", help="Recurring subscription charges with frequency, cost, and status")

    # ── recent ──
    recent_p = sub.add_parser("recent", help="Recent transactions (split-aware)")
    recent_p.add_argument("-n", "--limit", type=int, default=25, help="Number of transactions (default: 25)")
    recent_p.add_argument("--account", help="Filter by account name (partial match)")
    recent_p.add_argument("--payee", help="Filter by payee name (partial match)")
    recent_p.add_argument("--category", help="Filter by category name (partial match)")
    recent_p.add_argument("--memo", help="Filter by memo text (partial match)")
    recent_p.add_argument("--uncleared", action="store_true", help="Show only uncleared/pending transactions")
    recent_p.add_argument("--month", help="Filter to a specific month (YYYY-MM)")

    # ── large ──
    large_p = sub.add_parser("large", help="Large expenses over threshold")
    large_p.add_argument("--threshold", type=float, default=500, help="Amount threshold (default: 500)")
    large_p.add_argument("--months", type=int, default=6, help="Months of history (default: 6)")

    # ── income ──
    income_p = sub.add_parser("income", help="Income breakdown: regular pay, bonuses, YTD")
    income_p.add_argument("--months", type=int, default=12, help="Months of history (default: 12)")

    # ── paycheck ──
    paycheck_p = sub.add_parser("paycheck", help="Paycheck forecast: detect income sources, project forward")
    paycheck_p.add_argument("--months", type=int, default=12, help="Months of history for detection (default: 12)")

    # ── bonus-split ──
    bonus_split_p = sub.add_parser(
        "bonus-split",
        help="Split bonus paycheck: move bonus portion to Holding: Next Month. See docs/two-pot-methodology.md",
    )
    bonus_split_p.add_argument(
        "--paycheck-amount",
        type=float,
        dest="paycheck_amount",
        help="Total paycheck amount (auto-detected if not specified)",
    )
    bonus_split_p.add_argument(
        "--regular-pay",
        type=float,
        dest="regular_pay",
        default=None,
        help="Regular (non-bonus) pay amount (default: value of YNAB_REGULAR_PAY env var)",
    )
    bonus_split_p.add_argument("--month", help="Target budget month (YYYY-MM, default: current)")
    bonus_split_p.add_argument("--apply", action="store_true", help="Apply the bonus split (default: dry-run)")

    # ── bonus_split (deprecated alias) ──
    bonus_p = sub.add_parser(
        "bonus_split",
        help="DEPRECATED: use bonus-split instead",
    )
    bonus_p.add_argument(
        "--paycheck-amount",
        type=float,
        dest="paycheck_amount",
        help="Total paycheck amount (auto-detected if not specified)",
    )
    bonus_p.add_argument(
        "--regular-pay",
        type=float,
        dest="regular_pay",
        default=None,
        help="Regular (non-bonus) pay amount (default: value of YNAB_REGULAR_PAY env var)",
    )
    bonus_p.add_argument("--month", help="Target budget month (YYYY-MM, default: current)")
    bonus_p.add_argument("--apply", action="store_true", help="Apply the bonus split (default: dry-run)")

    # ── breakdown ──
    breakdown_p = sub.add_parser(
        "breakdown", help="Paycheck budget breakdown: regular vs bonus-funded. See docs/two-pot-methodology.md"
    )
    breakdown_p.add_argument("--month", help="Month (YYYY-MM, default: current)")

    # ── paycheck-funding ──
    pf_p = sub.add_parser(
        "paycheck-funding",
        help="Tier-based paycheck funding plan: preview or apply. See docs/two-pot-methodology.md",
    )
    pf_p.add_argument("--month", help="Target budget month (YYYY-MM, default: current)")
    pf_p.add_argument("--apply", action="store_true", help="Apply the funding plan (default: dry-run preview)")
    pf_p.add_argument(
        "--through-tier",
        type=int,
        default=2,
        dest="through_tier",
        metavar="N",
        help="Apply through tier N (1-5, default: 2). Only used with --apply.",
    )

    # ── paycheck_funding (deprecated alias) ──
    pf_dep_p = sub.add_parser(
        "paycheck_funding",
        help="DEPRECATED: use paycheck-funding instead",
    )
    pf_dep_p.add_argument("--month", help="Target budget month (YYYY-MM, default: current)")
    pf_dep_p.add_argument("--apply", action="store_true", help="Apply the funding plan (default: dry-run preview)")
    pf_dep_p.add_argument(
        "--through-tier",
        type=int,
        default=2,
        dest="through_tier",
        metavar="N",
        help="Apply through tier N (1-5, default: 2). Only used with --apply.",
    )

    # ── fund ──
    # fund accepts: subcommands (status, log, goals) OR positional category+amount.
    # Subcommands are detected at dispatch time via args.category to avoid argparse
    # subparser conflicts with free-text category names like "Groceries".
    fund_p = sub.add_parser(
        "fund",
        help="Set/adjust category budgeted amounts",
        description=(
            "Set or adjust a category budget amount, or use a subcommand:\n"
            "  ynab fund status        -- show funding analysis\n"
            "  ynab fund log [N]       -- show last N funding log entries (default 20)\n"
            "  ynab fund goals         -- preview mass-funding all goals\n"
            "  ynab fund goals --apply -- apply goal funding\n"
            "\n"
            "Note: 'status', 'log', and 'goals' are reserved subcommand names.\n"
            "If your category is literally named one of these, use group disambiguation:\n"
            '  ynab fund "Monthly Bills: goals" 500\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fund_p.add_argument(
        "category",
        nargs="?",
        help="Subcommand (status|log|goals) or category name for direct funding",
    )
    fund_p.add_argument("amount", nargs="?", help="Amount to set (600) or adjust (+100, -50)")
    fund_p.add_argument("--month", help="Target month (YYYY-MM, default: current)")
    fund_p.add_argument("--goals", action="store_true", help="[DEPRECATED] Use: ynab fund goals")
    fund_p.add_argument("--apply", action="store_true", help="Apply goal funding (default: dry-run)")
    fund_p.add_argument("--status", action="store_true", help="[DEPRECATED] Use: ynab fund status")
    fund_p.add_argument(
        "--log",
        nargs="?",
        const=20,
        type=int,
        metavar="N",
        help="[DEPRECATED] Use: ynab fund log",
    )

    # ── audit ──
    audit_p = sub.add_parser("audit", help="View audit log of all ynab-tools actions")
    audit_p.add_argument("-n", "--limit", type=int, default=30, help="Number of entries (default: 30)")
    audit_p.add_argument("--action", help="Filter by action (e.g. set-goal, create-transaction)")

    # ── sinking-funds ──
    sub.add_parser("sinking-funds", help="Sinking fund balances and goal status")

    # ── retirement ──
    ret_p = sub.add_parser("retirement", help="Retirement balances, contributions, projections")
    ret_p.add_argument("--year", type=int, help="Year to show contributions for (default: current)")
    ret_p.add_argument("--project", action="store_true", help="Show growth projections to retirement")
    ret_p.add_argument("--debug", action="store_true", help="Print per-transaction classification for troubleshooting")

    # ── reconcile ──
    rec_p = sub.add_parser("reconcile", help="Reconcile account to real-world balance")
    rec_p.add_argument("account", nargs="?", help="Account name (partial match)")
    rec_p.add_argument("balance", nargs="?", type=float, help="Actual current balance (positive number)")
    rec_p.add_argument("--apply", action="store_true", help="Apply the adjustment via YNAB API")

    # ── add ──
    add_p = sub.add_parser("add", help="Create a new transaction")
    add_p.add_argument("account", help="Account name (partial match)")
    add_p.add_argument("amount", type=float, help="Amount in dollars (positive number)")
    add_p.add_argument("--payee", required=True, help="Payee name")
    add_p.add_argument("--date", help="Transaction date YYYY-MM-DD (default: today)")
    add_p.add_argument("--memo", help="Transaction memo")
    add_p.add_argument("--inflow", action="store_true", help="Record as inflow (refund/payment); default is outflow")
    add_p.add_argument(
        "--cleared",
        choices=["cleared", "uncleared", "reconciled"],
        default="cleared",
        help="Clear status (default: cleared)",
    )
    add_p.add_argument("--category", help="Category name (partial match)")
    add_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    # ── update ──
    upd_p = sub.add_parser("update", help="Update an existing transaction")
    upd_p.add_argument("transaction_id", help="Transaction ID (or unique prefix)")
    upd_p.add_argument("--category", help="New category name (partial match)")
    upd_p.add_argument("--memo", help="New memo text")
    upd_p.add_argument(
        "--split",
        nargs="+",
        metavar=("CATEGORY", "AMOUNT"),
        dest="split_args",
        help=(
            "Split transaction: alternating category name and dollar amount pairs. "
            "Example: --split 'Home: Decor' 129.30 'Home: Household Supplies' 44.61"
        ),
    )
    upd_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    # ── delete ──
    del_p = sub.add_parser("delete", help="Delete a transaction from YNAB")
    del_p.add_argument("transaction_id", help="Transaction ID (or unique prefix)")
    del_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    # ── unapproved ──
    sub.add_parser("unapproved", help="List unapproved transactions needing review")

    # ── approve ──
    appr_p = sub.add_parser("approve", help="Approve unapproved transactions")
    appr_p.add_argument("transaction_id", nargs="?", help="Transaction ID (or unique prefix)")
    appr_p.add_argument(
        "--all",
        action="store_true",
        dest="all_categorized",
        help="Approve all categorized transactions",
    )
    appr_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    # ── category ──
    cat_mgmt_p = sub.add_parser("category", help="Category management (create, set-goal)")
    cat_sub = cat_mgmt_p.add_subparsers(dest="cat_command", help="Category subcommand")

    cat_grp_p = cat_sub.add_parser("create-group", help="Create a new category group")
    cat_grp_p.add_argument("name", help="Category group name")
    cat_grp_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    cat_create_p = cat_sub.add_parser("create", help="Create a new budget category")
    cat_create_p.add_argument("name", help="Category name (e.g. 'Savings: General')")
    cat_create_p.add_argument("--group", required=True, help="Category group name (partial match)")
    cat_create_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    cat_goal_p = cat_sub.add_parser("set-goal", help="Set a target/goal on a category")
    cat_goal_p.add_argument("category", help="Category name (partial match)")
    cat_goal_p.add_argument("amount", type=float, help="Monthly target amount in dollars")
    cat_goal_p.add_argument(
        "--type", choices=["MF", "TB", "TBD", "NEED"], default="MF", help="Goal type (default: MF = monthly funding)"
    )
    cat_goal_p.add_argument("--by-date", help="Target date for TBD type (YYYY-MM)")
    cat_goal_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    cat_clear_p = cat_sub.add_parser("clear-goal", help="Remove goal from a category")
    cat_clear_p.add_argument("category", help="Category name (partial match)")
    cat_clear_p.add_argument("--apply", action="store_true", help="Skip confirmation prompt")

    # ── plan ──
    plan_p = sub.add_parser("plan", help="Track planned expenses with funding gaps")
    plan_sub = plan_p.add_subparsers(dest="plan_command", help="Plan subcommand")

    plan_sub.add_parser("list", help="List active planned expenses")

    plan_add_p = plan_sub.add_parser("add", help="Add a planned expense")
    plan_add_p.add_argument("category", help="Category name (partial match)")
    plan_add_p.add_argument("amount", type=float, help="Amount needed in dollars")
    plan_add_p.add_argument("--by", required=True, dest="by_date", help="Due date (YYYY-MM-DD)")
    plan_add_p.add_argument("--memo", help="Description or note")

    plan_done_p = plan_sub.add_parser("done", help="Mark a planned expense as completed")
    plan_done_p.add_argument("id", type=int, help="Planned expense ID")

    plan_edit_p = plan_sub.add_parser("edit", help="Edit a planned expense")
    plan_edit_p.add_argument("id", type=int, help="Planned expense ID")
    plan_edit_p.add_argument("--amount", type=float, help="New amount in dollars")
    plan_edit_p.add_argument("--by", dest="by_date", help="New due date (YYYY-MM-DD)")
    plan_edit_p.add_argument("--memo", help="New memo/description")
    plan_edit_p.add_argument("--category", help="New category (partial match)")

    plan_remove_p = plan_sub.add_parser("remove", help="Remove a planned expense")
    plan_remove_p.add_argument("id", type=int, help="Planned expense ID")

    # ── two-pot ──
    two_pot_p = sub.add_parser(
        "two-pot",
        help="Two-pot compliance: structural backwards funding per bonus month",
    )
    two_pot_p.add_argument(
        "--months",
        type=int,
        default=3,
        help="Number of recent bonus months to show (default: 3)",
    )
    two_pot_p.add_argument(
        "--month",
        help="Target a specific month (YYYY-MM)",
    )

    # ── plans ──
    sub.add_parser("plans", help="List YNAB budget plans")

    # ── month-end ──
    me_p = sub.add_parser("month-end", help="Consolidated month-end closeout report")
    me_p.add_argument("--month", help="Month to report (YYYY-MM, default: auto-detect)")

    # ── import ──
    imp_p = sub.add_parser("import", help="Import external data")
    imp_sub = imp_p.add_subparsers(dest="import_command", help="Import subcommand")

    brok_p = imp_sub.add_parser("brokerage", help="Convert brokerage CSV to YNAB format")
    brok_p.add_argument("file", help="Brokerage CSV file to convert")
    brok_p.add_argument("-o", "--output", help="Output file (default: stdout)")
    brok_p.add_argument("--preview", action="store_true", help="Preview transactions only")
    brok_p.add_argument("--push", action="store_true", help="Push transactions directly to YNAB via API")
    brok_p.add_argument("--account", help="Target YNAB account (required with --push)")

    boa_p = imp_sub.add_parser("boa", help="Convert Bank of America CSV to YNAB format")
    boa_p.add_argument("file", help="Bank of America transaction CSV file to convert")
    boa_p.add_argument("-o", "--output", help="Output file (default: stdout)")
    boa_p.add_argument("--preview", action="store_true", help="Preview transactions only")
    boa_p.add_argument("--push", action="store_true", help="Push transactions directly to YNAB via API")
    boa_p.add_argument("--account", help="Target YNAB account (required with --push)")

    pos_p = imp_sub.add_parser("positions", help="Import Fidelity portfolio positions for reconciliation")
    pos_p.add_argument("file", help="Fidelity Portfolio Positions CSV")
    pos_p.add_argument("--reconcile", action="store_true", help="Generate reconciliation commands")
    pos_p.add_argument("--apply", action="store_true", help="Apply reconciliation adjustments via YNAB API")

    # ── configure ──
    configure_p = sub.add_parser("configure", help="Set up YNAB credentials interactively")
    configure_p.add_argument("--show", action="store_true", help="Show current configuration values and their sources")
    configure_p.add_argument(
        "--reset",
        nargs="?",
        const=True,
        metavar="KEY",
        help="Remove KEY from config.json, or run interactively to pick a key to clear",
    )

    # ── dashboard ──
    dash_p = sub.add_parser("dashboard", help="Local web dashboard management")
    dash_sub = dash_p.add_subparsers(dest="dash_command", help="Dashboard subcommand")

    start_p = dash_sub.add_parser("start", help="Start dashboard in the foreground")
    start_p.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    start_p.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    start_p.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    start_p.add_argument(
        "--sync-interval",
        type=int,
        default=0,
        metavar="MINUTES",
        help="Auto-sync interval in minutes (default: 0 = disabled)",
    )

    inst_p = dash_sub.add_parser("install", help="Install as a macOS LaunchAgent (runs on login, restarts on crash)")
    inst_p.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    inst_p.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    inst_p.add_argument(
        "--sync-interval", type=int, default=60, metavar="MINUTES", help="Auto-sync interval in minutes (default: 60)"
    )
    inst_p.add_argument("--reload", action="store_true", help="Enable uvicorn file-watching for editable installs")

    dash_sub.add_parser("uninstall", help="Stop and remove the LaunchAgent")
    dash_sub.add_parser("restart", help="Restart the LaunchAgent (picks up upgrades)")
    dash_sub.add_parser("status", help="Show running status and recent log lines")
    logs_p = dash_sub.add_parser("logs", help="Print recent dashboard log lines")
    logs_p.add_argument("-n", type=int, default=50, metavar="LINES", help="Number of lines to show (default: 50)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # ── Dispatch ──

    if args.command == "configure":
        if getattr(args, "show", False):
            _cmd_configure_show()
        elif getattr(args, "reset", None) is not None:
            _cmd_configure_reset(args.reset if isinstance(args.reset, str) else None)
        else:
            _cmd_configure()
        return

    if args.command == "sync":
        from .sync import run_sync, show_status

        if args.status:
            show_status()
        else:
            run_sync(months=args.months, full=args.full)

    elif args.command == "payee":
        _dispatch_payee(args)

    elif args.command == "categorize":
        from .categories.classifier import run_categorize

        run_categorize(apply=args.apply)

    elif args.command == "split":
        from .categories.classifier import run_split

        run_split(index=args.index, apply=args.apply)

    elif args.command == "net-worth":
        from .reports.net_worth import run_snapshot

        run_snapshot(history=args.history, detail=args.detail)

    elif args.command == "budget":
        if args.cc_audit:
            from .reports.budget import run_cc_audit

            run_cc_audit()
        elif args.overspend_plan:
            from .reports.budget import run_overspend_plan

            run_overspend_plan(month=args.month)
        else:
            from .reports.budget import run_budget_check

            run_budget_check(month=args.month)

    elif args.command == "balance":
        from .reports.budget import run_balance

        run_balance(category=args.category)

    elif args.command == "transfers":
        from .reports.transactions import run_transfers

        run_transfers(days=args.days)

    elif args.command == "summary":
        from .reports.summary import run_summary

        run_summary(months=args.months)

    elif args.command == "spending":
        # "breakdown" subcommand via positional arg
        if args.subcommand == "breakdown":
            from .reports.spending import run_spending_breakdown

            run_spending_breakdown(months=args.months)
        elif args.breakdown:
            # Deprecated flag path - still works but warns
            print("spending --breakdown is deprecated. Use: ynab spending breakdown", file=sys.stderr)
            from .reports.spending import run_spending_breakdown

            run_spending_breakdown(months=args.months)
        elif args.subcommand is not None:
            spend_p.error(f"unknown spending subcommand '{args.subcommand}' -- valid subcommand: breakdown")
        else:
            from .reports.spending import run_spending

            run_spending(months=args.months)

    elif args.command == "spending-pace":
        from .reports.spending import run_spending_pace

        run_spending_pace(month=args.month)

    elif args.command == "trends":
        from .reports.spending import run_category_trend

        run_category_trend(category=args.category, months=args.months)

    elif args.command == "debt":
        from .reports.debt import run_debt_status

        run_debt_status()

    elif args.command == "subscriptions":
        from .reports.subscriptions import run_subscriptions

        run_subscriptions()

    elif args.command == "audit":
        from .reports.audit_log import run_audit_log

        run_audit_log(limit=args.limit, action=args.action)

    elif args.command == "income":
        from .reports.income import run_income

        run_income(months=args.months)

    elif args.command == "paycheck":
        from .reports.paycheck import run_paycheck

        run_paycheck(months=args.months)

    elif args.command == "bonus-split":
        from .reports.bonus_split import run_bonus_split

        run_bonus_split(
            regular_pay=args.regular_pay,
            paycheck_amount=args.paycheck_amount,
            month=args.month,
            apply=args.apply,
        )

    elif args.command == "bonus_split":
        print("bonus_split is deprecated. Use: ynab bonus-split", file=sys.stderr)
        from .reports.bonus_split import run_bonus_split

        run_bonus_split(
            regular_pay=args.regular_pay,
            paycheck_amount=args.paycheck_amount,
            month=args.month,
            apply=args.apply,
        )

    elif args.command == "breakdown":
        from .reports.paycheck_breakdown import run_paycheck_breakdown

        run_paycheck_breakdown(month=args.month)

    elif args.command == "two-pot":
        from .reports.two_pot_report import run_two_pot_report

        run_two_pot_report(months=args.months, month=args.month)

    elif args.command == "paycheck-funding":
        if args.apply:
            from .reports.paycheck_funding import run_paycheck_funding_apply

            run_paycheck_funding_apply(month=args.month, through_tier=args.through_tier)
        else:
            from .reports.paycheck_funding import run_paycheck_funding

            run_paycheck_funding(month=args.month)

    elif args.command == "paycheck_funding":
        print("paycheck_funding is deprecated. Use: ynab paycheck-funding", file=sys.stderr)
        if args.apply:
            from .reports.paycheck_funding import run_paycheck_funding_apply

            run_paycheck_funding_apply(month=args.month, through_tier=args.through_tier)
        else:
            from .reports.paycheck_funding import run_paycheck_funding

            run_paycheck_funding(month=args.month)

    elif args.command == "recent":
        from .reports.transactions import run_recent

        run_recent(
            limit=args.limit,
            account=args.account,
            payee=args.payee,
            category=args.category,
            memo=args.memo,
            uncleared=args.uncleared,
            month=args.month,
        )

    elif args.command == "large":
        from .reports.transactions import run_large

        run_large(threshold=args.threshold, months=args.months)

    elif args.command == "fund":
        # Subcommands are passed as the first positional (args.category) to avoid
        # argparse subparser conflicts with free-text category names.
        fund_cmd = args.category

        if fund_cmd == "status":
            from .reports.funding import run_fund_status

            run_fund_status(month=args.month)

        elif fund_cmd == "log":
            if args.amount is not None:
                try:
                    limit = int(args.amount)
                except (TypeError, ValueError):
                    fund_p.error(f"invalid log limit '{args.amount}' -- expected an integer")
            else:
                limit = 20
            from .reports.funding import run_fund_log

            run_fund_log(limit=limit, month=args.month)

        elif fund_cmd == "goals":
            from .reports.funding import run_fund_goals

            run_fund_goals(month=args.month, apply=args.apply)

        else:
            # Legacy flag path (deprecated) or positional category+amount
            if args.log is not None:
                print("fund --log is deprecated. Use: ynab fund log", file=sys.stderr)
                from .reports.funding import run_fund_log

                run_fund_log(limit=args.log, month=args.month)
            elif args.status:
                print("fund --status is deprecated. Use: ynab fund status", file=sys.stderr)
                from .reports.funding import run_fund_status

                run_fund_status(month=args.month)
            elif args.goals:
                print("fund --goals is deprecated. Use: ynab fund goals", file=sys.stderr)
                from .reports.funding import run_fund_goals

                run_fund_goals(month=args.month, apply=args.apply)
            elif args.category and args.amount:
                from .reports.funding import run_fund

                run_fund(category=args.category, amount_str=args.amount, month=args.month, apply=args.apply)
            else:
                fund_p.print_help()
                sys.exit(1)

    elif args.command == "sinking-funds":
        from .reports.transactions import run_sinking_funds

        run_sinking_funds()

    elif args.command == "retirement":
        from .reports.retirement import run_retirement

        run_retirement(year=args.year, project=args.project, debug=args.debug)

    elif args.command == "reconcile":
        if args.account and args.balance is not None:
            from .reports.reconcile import run_reconcile

            run_reconcile(account=args.account, balance=args.balance, apply=args.apply)
        elif args.account and args.balance is None:
            rec_p.error("balance is required when account is specified")
        else:
            from .reports.reconcile import run_reconcile_list

            run_reconcile_list()

    elif args.command == "add":
        from .reports.transactions import run_add

        run_add(
            account=args.account,
            amount=args.amount,
            payee=args.payee,
            date=args.date,
            memo=args.memo,
            inflow=args.inflow,
            cleared=args.cleared,
            apply=args.apply,
            category=args.category,
        )

    elif args.command == "update":
        from .reports.transactions import run_update

        splits = None
        if args.split_args:
            raw = args.split_args
            if len(raw) % 2 != 0:
                parser.error("--split requires alternating category and amount pairs (even number of arguments).")
            splits = []
            for i in range(0, len(raw), 2):
                try:
                    splits.append({"category": raw[i], "amount": float(raw[i + 1])})
                except ValueError:
                    parser.error(f"--split: '{raw[i + 1]}' is not a valid dollar amount.")

        run_update(
            transaction_id=args.transaction_id,
            category=args.category,
            memo=args.memo,
            splits=splits,
            apply=args.apply,
        )

    elif args.command == "delete":
        from .reports.transactions import run_delete

        run_delete(
            transaction_id=args.transaction_id,
            apply=args.apply,
        )

    elif args.command == "unapproved":
        from .reports.transactions import run_unapproved

        run_unapproved()

    elif args.command == "approve":
        from .reports.transactions import run_approve

        run_approve(
            transaction_id=args.transaction_id,
            all_categorized=args.all_categorized,
            apply=args.apply,
        )

    elif args.command == "category":
        if not args.cat_command:
            cat_mgmt_p.print_help()
            sys.exit(1)
        if args.cat_command == "create-group":
            from .reports.categories import run_create_group

            run_create_group(name=args.name, apply=args.apply)
        elif args.cat_command == "create":
            from .reports.categories import run_create_category

            run_create_category(name=args.name, group=args.group, apply=args.apply)
        elif args.cat_command == "set-goal":
            from .reports.categories import run_set_goal

            run_set_goal(
                category=args.category, amount=args.amount, goal_type=args.type, by_date=args.by_date, apply=args.apply
            )
        elif args.cat_command == "clear-goal":
            from .reports.categories import run_clear_goal

            run_clear_goal(category=args.category, apply=args.apply)

    elif args.command == "plan":
        from .reports.planned import run_plan_add, run_plan_done, run_plan_edit, run_plan_list, run_plan_remove

        cmd = args.plan_command
        if cmd == "add":
            run_plan_add(category=args.category, amount=args.amount, by_date=args.by_date, memo=args.memo)
        elif cmd == "edit":
            run_plan_edit(
                plan_id=args.id,
                amount=getattr(args, "amount", None),
                by_date=getattr(args, "by_date", None),
                memo=getattr(args, "memo", None),
                category=getattr(args, "category", None),
            )
        elif cmd == "done":
            run_plan_done(plan_id=args.id)
        elif cmd == "remove":
            run_plan_remove(plan_id=args.id)
        else:
            # Default: "ynab plan" with no subcommand = list
            run_plan_list()

    elif args.command == "plans":
        from .client import YNABClient
        from .config import require_token
        from .payees.audit import cmd_plans

        # Only the token is needed: /plans is a root endpoint, and this is the
        # command that tells you what your plan ID is.
        client = YNABClient(require_token(), "")
        cmd_plans(client)

    elif args.command == "month-end":
        from .reports.month_end import run_month_end

        run_month_end(month=args.month)

    elif args.command == "dashboard":
        _dispatch_dashboard(args)

    elif args.command == "import":
        if not args.import_command:
            imp_p.print_help()
            sys.exit(1)
        if args.import_command == "brokerage":
            from .importers.brokerage import run_import

            run_import(file=args.file, output=args.output, preview=args.preview, push=args.push, account=args.account)
        elif args.import_command == "boa":
            from .importers.bank_of_america import run_import as run_boa_import

            run_boa_import(
                file=args.file, output=args.output, preview=args.preview, push=args.push, account=args.account
            )
        elif args.import_command == "positions":
            from .importers.fidelity import run_positions

            run_positions(file=args.file, reconcile=args.reconcile, apply=args.apply)


def _dispatch_dashboard(args):
    """Route dashboard subcommands."""
    if not args.dash_command:
        from .dashboard.launchagent import cmd_status

        cmd_status()
        return

    cmd = args.dash_command

    if cmd == "start":
        try:
            import uvicorn
        except ImportError:
            print("Dashboard requires additional dependencies.")
            print("Install with: uv tool install 'ynab-tools[dashboard]'")
            sys.exit(1)
        from .dashboard import server as _srv

        interval = args.sync_interval
        if interval == 0:
            try:
                interval = int(os.environ.get("YNAB_DASHBOARD_SYNC_INTERVAL", "0"))
            except ValueError:
                interval = 0
        _srv._sync_interval_minutes = max(0, interval)
        # Fails closed on a non-loopback bind with no password set.
        _srv.require_password_for_remote_bind(args.host)
        print(f"Starting YNAB Dashboard at http://{args.host}:{args.port}")
        uvicorn.run("ynab_tools.dashboard.server:app", host=args.host, port=args.port, reload=args.reload)

    elif cmd == "install":
        from .dashboard.launchagent import cmd_install

        cmd_install(args.host, args.port, args.sync_interval, args.reload)

    elif cmd == "uninstall":
        from .dashboard.launchagent import cmd_uninstall

        cmd_uninstall()

    elif cmd == "restart":
        from .dashboard.launchagent import cmd_restart

        cmd_restart()

    elif cmd == "status":
        from .dashboard.launchagent import cmd_status

        cmd_status()

    elif cmd == "logs":
        from .dashboard.launchagent import cmd_logs

        cmd_logs(args.n)


def _dispatch_payee(args):
    """Route payee subcommands."""
    if not args.payee_command:
        print("Usage: ynab payee <subcommand>")
        print("Run 'ynab payee --help' for available subcommands.")
        sys.exit(1)

    cmd = args.payee_command

    # Commands that don't need DB or client
    if cmd == "validate":
        from .payees.audit import cmd_validate

        cmd_validate()
        return

    if cmd == "backups":
        from .payees.backup import cmd_list_backups

        cmd_list_backups()
        return

    if cmd == "plans":
        print("ynab payee plans is deprecated. Use: ynab plans", file=sys.stderr)

        from .client import YNABClient
        from .config import require_token
        from .payees.audit import cmd_plans

        cmd_plans(YNABClient(require_token(), ""))
        return

    # Commands that need client
    from .client import YNABClient
    from .config import require_credentials

    token, plan_id = require_credentials()
    client = YNABClient(token, plan_id)

    if cmd == "create":
        name = args.name
        if not args.apply:
            print(f"  Payee: {name}")
            print()
            try:
                confirm = input("Create payee? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("Cancelled. Use --apply to skip confirmation.")
                return
        result = client.create_payee(name)
        if result:
            print(f"Payee '{name}' created (ID: {result['id']}).")
            print("Run 'ynab sync' to update local data.")
        else:
            print("Failed to create payee. Check logs.")
        return

    # Commands that need DB + client
    from .db import get_connection

    conn = get_connection()

    if cmd == "audit":
        from .payees.audit import cmd_audit

        cmd_audit(conn)
    elif cmd == "preview":
        from .payees.audit import cmd_preview

        cmd_preview(conn)
    elif cmd == "fix":
        from .payees.audit import cmd_fix

        cmd_fix(conn, client)
    elif cmd == "normalize":
        from .payees.normalize import cmd_normalize

        cmd_normalize(client, apply=args.apply)
    elif cmd == "orphaned":
        from .payees.orphaned import cmd_list_orphaned

        cmd_list_orphaned(conn, client, use_api=args.api)
    elif cmd == "restore":
        from .payees.backup import cmd_restore

        cmd_restore(args.file, client)

    conn.close()


_CREDENTIAL_KEYS = {"access_token", "plan_id"}


def _cmd_configure_show() -> None:
    """Print all current configuration values with source (config.json / env var / not set)."""
    from ynab_tools.config import _CONFIG_KEY_TO_ENV, CONFIG_FILE, load_env

    load_env()

    file_config: dict = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                file_config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    print("\nYNAB Tools - Current Configuration")
    print(f"Config file: {CONFIG_FILE}")
    print("=" * 76)
    print(f"  {'Key':<34} {'Value':<28} Source")
    print("-" * 76)

    for config_key, env_var in _CONFIG_KEY_TO_ENV.items():
        env_value = os.environ.get(env_var, "")
        in_file = config_key in file_config
        file_value = str(file_config.get(config_key, "")).strip() if in_file else ""

        if env_value:
            if config_key == "access_token":
                display = ("****" + env_value[-6:]) if len(env_value) > 6 else "****"
            elif config_key in _CREDENTIAL_KEYS:
                display = (env_value[:8] + "...") if len(env_value) > 8 else env_value
            else:
                display = env_value if len(env_value) <= 40 else env_value[:37] + "..."
            source = "config.json" if (in_file and env_value == file_value) else "env var"
        else:
            display = "(not set)"
            source = ""

        print(f"  {config_key:<34} {display:<28} {source}")

    print()


def _cmd_configure_reset(key: str | None) -> None:
    """Remove a key from config.json, or interactively pick one to clear."""
    from ynab_tools.config import _CONFIG_KEY_TO_ENV, CONFIG_FILE

    resetable = [k for k in _CONFIG_KEY_TO_ENV if k not in _CREDENTIAL_KEYS]

    file_config: dict = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                file_config = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"Error: could not read {CONFIG_FILE}", file=sys.stderr)
            return

    if key is not None:
        if key in _CREDENTIAL_KEYS:
            print(f"Error: '{key}' is a credential and cannot be cleared with --reset.")
            print("Use 'ynab configure' to update credentials.")
            return
        if key not in resetable:
            print(f"Error: '{key}' is not a recognized config key.")
            print(f"Valid keys: {', '.join(sorted(resetable))}")
            return
        if key not in file_config:
            print(f"'{key}' is not set in {CONFIG_FILE} - nothing to remove.")
            return
        del file_config[key]
        _write_config(file_config, CONFIG_FILE)
        print(f"Removed '{key}' from {CONFIG_FILE}")
        return

    # Interactive mode
    currently_set = [k for k in resetable if k in file_config]
    if not currently_set:
        print("No optional settings are currently configured in config.json.")
        return

    print("\nCurrently configured optional settings:")
    for i, k in enumerate(currently_set, 1):
        val = str(file_config[k])
        short = val if len(val) <= 50 else val[:47] + "..."
        print(f"  {i}. {k} = {short}")
    print(f"  {len(currently_set) + 1}. All of the above")
    print("  0. Cancel")

    try:
        choice = input("\nSelect setting to remove [0]: ").strip() or "0"
        n = int(choice)
    except (ValueError, EOFError):
        n = 0

    if n == 0:
        print("Cancelled.")
    elif n == len(currently_set) + 1:
        for k in currently_set:
            del file_config[k]
        _write_config(file_config, CONFIG_FILE)
        print(f"Removed {len(currently_set)} optional settings from {CONFIG_FILE}")
    elif 1 <= n <= len(currently_set):
        chosen = currently_set[n - 1]
        del file_config[chosen]
        _write_config(file_config, CONFIG_FILE)
        print(f"Removed '{chosen}' from {CONFIG_FILE}")
    else:
        print("Invalid choice.")


def _write_config(config: dict, path) -> None:
    from ynab_tools.config import atomic_write_json

    atomic_write_json(path, config, dir_mode=0o700, file_mode=0o600)


def _cmd_configure():
    """Interactive setup wizard - writes credentials to ~/.config/ynab-tools/config.json."""
    from ynab_tools.config import CONFIG_DIR, CONFIG_FILE, load_env

    load_env()

    print("\nYNAB Tools - Configuration Setup")
    print("=" * 40)

    sources = [("1", "Enter manually")]
    if shutil.which("op"):
        sources.append((str(len(sources) + 1), "1Password (op detected)"))

    if len(sources) == 1:
        source = "manual"
    else:
        print("\nSource credentials from:")
        for num, label in sources:
            print(f"  {num}. {label}")
        choice = input("\nChoice [1]: ").strip() or "1"
        chosen_label = dict(sources).get(choice, "Enter manually")
        if "1Password" in chosen_label:
            source = "1password"
        else:
            source = "manual"

    print()

    if source == "1password":
        op_item = os.environ.get("YNAB_1PASSWORD_ITEM", "YNAB").strip() or "YNAB"
        print(f"Will look for a 1Password item named '{op_item}'.")
        print("(Set YNAB_1PASSWORD_ITEM in config.json or env to use a different item name.)\n")
        access_token = _fetch_1password(op_item, "access_token", "Personal access token")
        plan_id = _fetch_1password(op_item, "plan_id", "Budget/plan ID")
    else:
        access_token = getpass.getpass("Personal access token: ").strip()
        plan_id = ""

    if not access_token:
        print("\nError: access token is required.", file=sys.stderr)
        sys.exit(1)

    # No plan ID yet (manual entry, or the 1Password item had no plan_id
    # field): look the plans up with the token we just collected rather than
    # sending the user off to another command.
    if not plan_id:
        plan_id = _prompt_plan_id(access_token)

    # Patch only credential fields; preserve all other existing config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    existing["access_token"] = access_token
    if plan_id:
        existing["plan_id"] = plan_id

    _write_config(existing, CONFIG_FILE)

    print(f"\nSaved to {CONFIG_FILE}")
    if not plan_id:
        print("Run 'ynab plans' to find your plan ID, then re-run configure.")
    else:
        print("Run 'ynab sync --status' to verify connectivity.\n")


def _prompt_plan_id(access_token: str) -> str:
    """Ask YNAB which plans this token can see and let the user pick one.

    Falls back to a manual UUID prompt if the lookup fails (bad token, no
    network) so configure is never a dead end.
    """
    from .client import YNABClient

    print("\nLooking up your YNAB plans...")
    try:
        plans = YNABClient(access_token, "").get_plans()
    except Exception as e:  # network error, 401 on a bad token, malformed response
        print(f"Could not list plans: {e}")
        print("Enter the plan ID manually, or leave blank and re-run 'ynab configure' later.")
        return input("Budget/plan ID (UUID): ").strip()

    if not plans:
        print("No plans found for this token.")
        return input("Budget/plan ID (UUID): ").strip()

    if len(plans) == 1:
        only = plans[0]
        print(f"Found one plan: {only['name']}")
        return only["id"]

    print()
    for i, p in enumerate(plans, 1):
        print(f"  {i}. {p['name']}")
        print(f"     {p['id']}")
    choice = input(f"\nPlan [1-{len(plans)}, default 1]: ").strip() or "1"
    if choice.isdigit() and 1 <= int(choice) <= len(plans):
        return plans[int(choice) - 1]["id"]
    print("Not a listed choice - enter the plan ID manually.")
    return input("Budget/plan ID (UUID): ").strip()


def _fetch_1password(default_item: str, default_field: str, label: str) -> str:
    item_name = input(f"1Password item [{default_item}]: ").strip() or default_item
    field_name = input(f"Field name [{default_field}]: ").strip() or default_field
    try:
        result = subprocess.run(
            ["op", "item", "get", item_name, "--field", field_name],
            capture_output=True,
            text=True,
            check=True,
        )
        value = result.stdout.strip()
        if value:
            return value
        print(f"Empty value returned for field '{field_name}' in 1Password item '{item_name}'.")
        print("Check that the field exists in your vault item.")
        print("Falling back to manual entry.")
    except subprocess.CalledProcessError as e:
        print(f"1Password lookup for item '{item_name}' failed: {e.stderr.strip()}")
        print("Check that the item exists and you are signed in ('op signin').")
        print("Set YNAB_1PASSWORD_ITEM in config.json to your actual vault item name.")
        print("Falling back to manual entry.")
    is_secret = any(w in label.lower() for w in ("token", "password", "key"))
    return getpass.getpass(f"{label}: ").strip() if is_secret else input(f"{label}: ").strip()
