"""CLI entry point - argparse setup and command routing."""

import argparse
import json
import sys

from homeops import config as config_mod
from homeops import db
from homeops.cli import display


def main():
    parser = argparse.ArgumentParser(
        prog="homeops",
        description="Home maintenance operations CLI - tasks, pest control, providers, and cost tracking",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- configure ---
    subparsers.add_parser("configure", help="Set up Home Assistant credentials interactively")

    # --- task ---
    task_parser = subparsers.add_parser("task", help="Recurring maintenance tasks")
    task_sub = task_parser.add_subparsers(dest="task_command")

    task_sub.add_parser("list", help="List all active tasks with status")
    task_sub.add_parser("overdue", help="Show overdue tasks only")

    task_add = task_sub.add_parser("add", help="Add a recurring task")
    task_add.add_argument("name", help="Task name")
    task_add.add_argument("--interval", required=True, help="Recurrence interval (e.g. 30d, 90d, 6m, 1y)")
    task_add.add_argument("--category", required=True, help="Category (e.g. hvac, gutters, pest, safety)")
    task_add.add_argument("--notes", help="Notes")

    task_done = task_sub.add_parser("done", help="Mark a task as completed")
    task_done.add_argument("name", help="Task name (fuzzy match)")
    task_done.add_argument("--cost", type=float, help="Cost of this completion")
    task_done.add_argument("--provider", help="Service provider")
    task_done.add_argument("--notes", help="Notes")
    task_done.add_argument("--date", help="Completion date (YYYY-MM-DD, default: today)")

    task_pause = task_sub.add_parser("pause", help="Pause a task")
    task_pause.add_argument("name", help="Task name (fuzzy match)")

    task_resume = task_sub.add_parser("resume", help="Resume a paused task")
    task_resume.add_argument("name", help="Task name (fuzzy match)")

    task_history = task_sub.add_parser("history", help="Show completion history for a task")
    task_history.add_argument("name", help="Task name (fuzzy match)")

    task_sub.add_parser(
        "escalate",
        help="Report significantly overdue tasks (safety, or 60+ days)",
    )

    # --- pest ---
    pest_parser = subparsers.add_parser("pest", help="Pest control treatment tracking")
    pest_sub = pest_parser.add_subparsers(dest="pest_command")

    pest_add = pest_sub.add_parser("add", help="Log a pest treatment")
    pest_add.add_argument("--date", required=True, help="Treatment date (YYYY-MM-DD)")
    pest_add.add_argument("--area", required=True, help="Area treated (perimeter, interior, garage, attic)")
    pest_add.add_argument("--product", required=True, help="Product used")
    pest_add.add_argument("--method", help="Method (spray, bait, granular, trap)")
    pest_add.add_argument("--cost", type=float, help="Cost")
    pest_add.add_argument("--notes", help="Notes")

    pest_history = pest_sub.add_parser("history", help="View treatment history")
    pest_history.add_argument("--year", help="Filter by year (YYYY)")

    # --- provider ---
    prov_parser = subparsers.add_parser("provider", help="Service provider directory")
    prov_sub = prov_parser.add_subparsers(dest="provider_command")

    prov_add = prov_sub.add_parser("add", help="Add a service provider")
    prov_add.add_argument("name", help="Provider name")
    prov_add.add_argument("--category", required=True, help="Category (e.g. hvac, gutters, pest)")
    prov_add.add_argument("--phone", help="Phone number")
    prov_add.add_argument("--email", help="Email")
    prov_add.add_argument("--cost", type=float, help="Typical cost per visit")
    prov_add.add_argument("--notes", help="Notes")

    prov_list = prov_sub.add_parser("list", help="List providers")
    prov_list.add_argument("--category", help="Filter by category")

    prov_show = prov_sub.add_parser("show", help="Show provider detail with cost history")
    prov_show.add_argument("name", help="Provider name (fuzzy match)")

    # --- cost ---
    cost_parser = subparsers.add_parser("cost", help="Maintenance cost tracking")
    cost_sub = cost_parser.add_subparsers(dest="cost_command")

    cost_add = cost_sub.add_parser("add", help="Log a maintenance cost")
    cost_add.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    cost_add.add_argument("--category", required=True, help="Category (e.g. hvac, gutters, pest)")
    cost_add.add_argument("--amount", type=float, required=True, help="Amount in dollars")
    cost_add.add_argument("--provider", help="Service provider")
    cost_add.add_argument("--description", help="Description")
    cost_add.add_argument("--notes", help="Notes")

    cost_summary = cost_sub.add_parser("summary", help="Spending summary by category")
    cost_summary.add_argument("--year", help="Filter by year (YYYY)")

    cost_history = cost_sub.add_parser("history", help="Cost line items")
    cost_history.add_argument("--year", help="Filter by year (YYYY)")
    cost_history.add_argument("--category", help="Filter by category")

    # --- appliance ---
    app_parser = subparsers.add_parser("appliance", aliases=["app"], help="Appliance lifecycle tracking")
    app_sub = app_parser.add_subparsers(dest="appliance_command")

    app_add = app_sub.add_parser("add", help="Add an appliance")
    app_add.add_argument("name", help="Appliance name")
    app_add.add_argument("--category", required=True, help="Category (e.g. hvac, plumbing, kitchen)")
    app_add.add_argument("--brand", help="Brand name")
    app_add.add_argument("--model", help="Model number")
    app_add.add_argument("--purchased", help="Purchase date (YYYY-MM-DD or YYYY-MM)")
    app_add.add_argument("--warranty-end", help="Warranty end date (YYYY-MM-DD or YYYY-MM)")
    app_add.add_argument("--lifespan", type=int, help="Expected lifespan in years")
    app_add.add_argument("--replacement-cost", type=float, help="Estimated replacement cost")
    app_add.add_argument("--location", help="Location in house")
    app_add.add_argument("--notes", help="Notes")

    app_list = app_sub.add_parser("list", help="List all appliances with age and warranty status")
    app_list.add_argument("--category", help="Filter by category")

    app_sub.add_parser("expiring", help="Appliances with warranties expiring within 12 months")
    app_sub.add_parser("aging", help="Appliances within 2 years of expected end of life")

    # --- utility ---
    util_parser = subparsers.add_parser("utility", aliases=["util"], help="Utility bill tracking")
    util_sub = util_parser.add_subparsers(dest="utility_command")

    util_add = util_sub.add_parser("add", help="Log a utility bill")
    util_add.add_argument("--date", required=True, help="Bill month (YYYY-MM)")
    util_add.add_argument(
        "--type", required=True, dest="util_type", help="Utility type (water, electric, gas, internet, trash)"
    )
    util_add.add_argument("--amount", type=float, required=True, help="Amount in dollars")
    util_add.add_argument("--usage", help="Usage amount (e.g. '12,000 gal', '1,200 kWh')")
    util_add.add_argument("--notes", help="Notes")

    util_trend = util_sub.add_parser("trend", help="Monthly trend for a utility type")
    util_trend.add_argument("util_type", help="Utility type (water, electric, gas)")
    util_trend.add_argument("--months", type=int, default=12, help="Months of history (default: 12)")

    util_summary = util_sub.add_parser("summary", help="Utility spending summary")
    util_summary.add_argument("--year", help="Filter by year (YYYY)")

    util_history = util_sub.add_parser("history", help="All utility bills")
    util_history.add_argument("--months", type=int, default=12, help="Months of history (default: 12)")

    util_sub.add_parser(
        "check-anomaly",
        help="Report bills >20%% above their trailing baseline average",
    )

    # --- status ---
    subparsers.add_parser("status", help="Dashboard - overview of all homeops data")

    # --- checklist ---
    check_parser = subparsers.add_parser("checklist", help="Seasonal maintenance checklists")
    check_sub = check_parser.add_subparsers(dest="checklist_command")

    check_show = check_sub.add_parser("show", help="Preview a seasonal checklist")
    check_show.add_argument("season", choices=["spring", "summer", "fall", "winter"], help="Season to show")

    check_load = check_sub.add_parser("load", help="Load checklist tasks into database")
    check_load.add_argument("season", choices=["spring", "summer", "fall", "winter"], help="Season to load")

    check_sub.add_parser("list", help="List available seasonal checklists")

    # --- budget ---
    budget_parser = subparsers.add_parser("budget", help="YNAB budget planning overview")
    budget_sub = budget_parser.add_subparsers(dest="budget_command")
    budget_sub.add_parser("overview", help="Comprehensive budget planning overview")
    budget_sub.add_parser("sinking", help="Appliance replacement sinking fund plan")
    budget_sub.add_parser("upcoming", help="Upcoming maintenance costs (next 90 days)")
    budget_sub.add_parser("utilities", help="Utility budget recommendations")

    # --- hvac ---
    hvac_parser = subparsers.add_parser("hvac", help="HVAC snapshot capture and trend analysis")
    hvac_sub = hvac_parser.add_subparsers(dest="hvac_command")

    hvac_snap = hvac_sub.add_parser("snapshot", help="Capture current HVAC state to DB")
    hvac_snap.add_argument("--dry-run", action="store_true", help="Show what would be captured without writing")

    hvac_trend = hvac_sub.add_parser("trend", help="Hourly temp and activity trend")
    hvac_trend.add_argument("--zone", help="Filter to a single zone name")
    hvac_trend.add_argument("--days", type=int, default=14, help="Days of history (default: 14)")

    hvac_modes = hvac_sub.add_parser("modes", help="Mode distribution and estimated runtime")
    hvac_modes.add_argument("--zone", help="Filter to a single zone name")
    hvac_modes.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")

    hvac_eff = hvac_sub.add_parser("efficiency", help="Runtime vs electric bill correlation")
    hvac_eff.add_argument("--months", type=int, default=3, help="Months of history (default: 3)")

    # --- db ---
    db_parser = subparsers.add_parser("db", help="Database management")
    db_sub = db_parser.add_subparsers(dest="db_command")
    db_sub.add_parser("init", help="Initialize the database")

    log_export_parser = db_sub.add_parser(
        "log-export", help="Seed the home_log markdown backend from existing SQLite rows"
    )
    log_export_parser.add_argument(
        "--preview", action="store_true", help="Print row counts without writing the markdown file(s)"
    )

    # Parse and route
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "configure":
            _cmd_configure()
            return

        config = config_mod.load_config()

        if args.command == "status":
            _handle_status(config)
        elif args.command == "checklist":
            _handle_checklist(args, config)
        elif args.command == "db":
            _handle_db(args, config)
        elif args.command == "task":
            _handle_task(args, config)
        elif args.command == "pest":
            _handle_pest(args, config)
        elif args.command == "provider":
            _handle_provider(args, config)
        elif args.command == "cost":
            _handle_cost(args, config)
        elif args.command == "budget":
            _handle_budget(args, config)
        elif args.command in ("appliance", "app"):
            _handle_appliance(args, config)
        elif args.command in ("utility", "util"):
            _handle_utility(args, config)
        elif args.command == "hvac":
            _handle_hvac(args, config)
        else:
            parser.print_help()

    except RuntimeError as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


def _handle_status(config):
    from homeops.status import get_status

    data = get_status(config)
    display.print_status(data)


def _handle_checklist(args, config):
    from homeops.checklists import ALL_CHECKLISTS, get_checklist, load_checklist

    if args.checklist_command == "list":
        display.print_checklist_list(ALL_CHECKLISTS)
    elif args.checklist_command == "show":
        checklist = get_checklist(args.season)
        display.print_checklist_preview(checklist)
    elif args.checklist_command == "load":
        result = load_checklist(config, args.season)
        display.print_checklist_loaded(result)
    else:
        print("Usage: homeops checklist {list|show|load} [season]")


def _handle_db(args, config):
    if args.db_command == "init":
        db_path = db.init_db(config)
        display.print_db_init(db_path)
    elif args.db_command == "log-export":
        from homeops.log_migrate import export_log

        preview = getattr(args, "preview", False)
        counts = export_log(config, preview=preview)
        display.print_log_export(counts, preview)
    else:
        print("Usage: homeops db {init|log-export}")


def _handle_task(args, config):
    if args.task_command == "list":
        tasks = db.list_tasks(config)
        display.print_task_list(tasks)
    elif args.task_command == "overdue":
        tasks = db.get_overdue(config)
        display.print_overdue(tasks)
    elif args.task_command == "add":
        result = db.add_task(config, args.name, args.category, args.interval, args.notes)
        display.print_task_added(result)
    elif args.task_command == "done":
        result = db.mark_done(config, args.name, args.cost, args.provider, args.notes, args.date)
        display.print_task_done(result)
    elif args.task_command == "pause":
        result = db.pause_task(config, args.name)
        print(f"\nTask paused: {result['name']}\n")
    elif args.task_command == "resume":
        result = db.resume_task(config, args.name)
        print(f"\nTask resumed: {result['name']}\n")
    elif args.task_command == "history":
        history = db.get_task_history(config, args.name)
        display.print_task_history(history)
    elif args.task_command == "escalate":
        _cmd_task_escalate(config)
    else:
        print("Usage: homeops task {list|overdue|add|done|pause|resume|history|escalate}")


def _cmd_task_escalate(config):
    """Deterministic replacement for the old `claude -p`-based
    task-escalation.sh LaunchAgent script (issue #146). Flags any overdue
    safety-category task and any non-safety task overdue more than 60 days.
    Read-only report: prints the qualifying tasks (issue #39 - Apple
    Reminders creation removed, no delivery channel left).
    """
    qualifying = db.evaluate_task_escalation(config)

    if not qualifying:
        print("Task escalation check: no qualifying tasks")
        return

    lines = [f"{t['name']} ({t['category']}): {t['days_overdue']} days overdue - {t['reason']}" for t in qualifying]

    plural = "" if len(qualifying) == 1 else "s"
    print(f"Task escalation check: {len(qualifying)} task{plural} qualify")
    for line in lines:
        print(f"  - {line}")


def _handle_pest(args, config):
    if args.pest_command == "add":
        result = db.add_pest_treatment(
            config,
            args.date,
            args.area,
            args.product,
            args.method,
            args.notes,
            args.cost,
        )
        display.print_pest_added(result)
    elif args.pest_command == "history":
        history = db.list_pest_history(config, args.year if hasattr(args, "year") else None)
        display.print_pest_history(history)
    else:
        print("Usage: homeops pest {add|history}")


def _handle_provider(args, config):
    if args.provider_command == "add":
        result = db.add_provider(
            config,
            args.name,
            args.category,
            args.phone,
            args.email,
            args.cost,
            args.notes,
        )
        display.print_provider_added(result)
    elif args.provider_command == "list":
        providers = db.list_providers(config, args.category if hasattr(args, "category") else None)
        display.print_provider_list(providers)
    elif args.provider_command == "show":
        provider = db.get_provider_detail(config, args.name)
        display.print_provider_detail(provider)
    else:
        print("Usage: homeops provider {add|list|show}")


def _handle_cost(args, config):
    if args.cost_command == "add":
        result = db.add_cost(
            config,
            args.date,
            args.category,
            args.amount,
            args.provider,
            args.description,
            args.notes,
        )
        print(f"\nCost logged: ${result['amount']:.2f} ({result['category']})\n")
    elif args.cost_command == "summary":
        summary = db.get_cost_summary(config, args.year if hasattr(args, "year") else None)
        display.print_cost_summary(summary, args.year if hasattr(args, "year") else None)
    elif args.cost_command == "history":
        costs = db.get_cost_history(
            config,
            args.year if hasattr(args, "year") else None,
            args.category if hasattr(args, "category") else None,
        )
        display.print_cost_history(costs)
    else:
        print("Usage: homeops cost {add|summary|history}")


def _handle_budget(args, config):
    from homeops.ynab_bridge import (
        appliance_sinking_fund_plan,
        budget_overview,
        upcoming_maintenance_costs,
        utility_budget_recommendation,
    )

    if args.budget_command == "overview":
        data = budget_overview(config)
        display.print_budget_overview(data)
    elif args.budget_command == "sinking":
        data = appliance_sinking_fund_plan(config)
        display.print_sinking_fund_plan(data)
    elif args.budget_command == "upcoming":
        data = upcoming_maintenance_costs(config)
        display.print_upcoming_maintenance(data)
    elif args.budget_command == "utilities":
        data = utility_budget_recommendation(config)
        display.print_utility_budget_rec(data)
    else:
        print("Usage: homeops budget {overview|sinking|upcoming|utilities}")


def _handle_utility(args, config):
    if args.utility_command == "add":
        result = db.add_utility_bill(
            config,
            args.date,
            args.util_type,
            args.amount,
            args.usage,
            args.notes,
        )
        display.print_utility_added(result)
    elif args.utility_command == "trend":
        bills = db.get_utility_trend(config, args.util_type, args.months)
        display.print_utility_trend(bills, args.util_type)
    elif args.utility_command == "summary":
        summary = db.get_utility_summary(config, args.year if hasattr(args, "year") else None)
        display.print_utility_summary(summary, args.year if hasattr(args, "year") else None)
    elif args.utility_command == "history":
        bills = db.get_all_utility_history(config, args.months)
        display.print_utility_history(bills)
    elif args.utility_command == "check-anomaly":
        _cmd_utility_check_anomaly(config)
    else:
        print("Usage: homeops utility {add|trend|summary|history|check-anomaly}")


def _cmd_utility_check_anomaly(config):
    """Deterministic replacement for the old `claude -p`-based
    utility-anomaly.sh LaunchAgent script (issue #146). Flags utility bills
    more than 20% above their trailing baseline average. Read-only report:
    prints the anomalies found (issue #39 - Apple Reminders creation
    removed, no delivery channel left).
    """
    anomalies = db.evaluate_utility_anomalies(config)

    if not anomalies:
        print("Utility anomaly check: no anomalies")
        return

    types = ", ".join(a["type"] for a in anomalies)
    plural = "y" if len(anomalies) == 1 else "ies"
    print(f"Utility anomaly check: {len(anomalies)} anomal{plural} found ({types})")
    for a in anomalies:
        print(
            f"  - {a['type']}: latest ${a['latest_amount']:.2f} vs baseline avg "
            f"${a['baseline_avg']:.2f} ({a['pct_over']:.0f}% over)"
        )
    print("  Check HVAC efficiency, leaks, or rate changes.")


def _handle_hvac(args, config):
    if args.hvac_command == "snapshot":
        from homeops.ha import hvac_status

        data = hvac_status()
        if args.dry_run:
            display.print_hvac_snapshot_dry_run(data)
        else:
            result = db.record_snapshot(config, data["zones"], data["outdoor"])
            display.print_hvac_snapshot_done(result, data)
    elif args.hvac_command == "trend":
        rows = db.get_hvac_trend(config, args.zone, args.days)
        display.print_hvac_trend(rows, args.days, args.zone)
    elif args.hvac_command == "modes":
        rows = db.get_mode_distribution(config, args.zone, args.days)
        display.print_hvac_modes(rows, args.days, args.zone)
    elif args.hvac_command == "efficiency":
        rows = db.get_hvac_efficiency(config, args.months)
        display.print_hvac_efficiency(rows, args.months)
    else:
        print("Usage: homeops hvac {snapshot|trend|modes|efficiency}")


def _handle_appliance(args, config):
    if args.appliance_command == "add":
        result = db.add_appliance(
            config,
            args.name,
            args.category,
            args.brand,
            args.model,
            args.purchased,
            getattr(args, "warranty_end", None),
            args.lifespan if hasattr(args, "lifespan") else None,
            getattr(args, "replacement_cost", None),
            args.location if hasattr(args, "location") else None,
            args.notes,
        )
        display.print_appliance_added(result)
    elif args.appliance_command == "list":
        appliances = db.list_appliances(config, args.category if hasattr(args, "category") else None)
        display.print_appliance_list(appliances)
    elif args.appliance_command == "expiring":
        appliances = db.get_expiring_warranties(config)
        display.print_appliance_expiring(appliances)
    elif args.appliance_command == "aging":
        appliances = db.get_aging_appliances(config)
        display.print_appliance_aging(appliances)
    else:
        print("Usage: homeops appliance {add|list|expiring|aging}")


def _write_config_atomic(config_file, data):
    """Write config.json via temp-file-then-rename, so a process kill
    mid-write (Ctrl-C, OOM-kill, host reboot) never leaves the file
    truncated or invalid. Same pattern already used for mail-tools'
    gmail_tokens.json/sender_rules.json (issue #71).
    """
    tmp = config_file.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        tmp.chmod(0o600)
        tmp.replace(config_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _cmd_configure():
    """Interactive setup wizard - writes homeops configuration to
    ~/.config/homeops/config.json.

    As of issue #143, HVAC tools query Prometheus (no auth required) rather
    than Home Assistant directly, so this wizard only prompts for
    prometheus_url - there's no credential to collect or 1Password lookup
    to offer anymore.
    """
    from homeops.config import CONFIG_DIR, CONFIG_FILE, DEFAULT_PROMETHEUS_URL

    print("\nHomeOps - Configuration Setup")
    print("=" * 40)
    print("HVAC tools query Prometheus (Home Assistant's own climate metrics")
    print("exporter), not Home Assistant directly - no credentials needed.\n")

    prometheus_url = input(f"Prometheus URL [{DEFAULT_PROMETHEUS_URL}]: ").strip() or DEFAULT_PROMETHEUS_URL

    # Patch only the prometheus_url field; preserve all other existing config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    existing["prometheus_url"] = prometheus_url.rstrip("/")

    _write_config_atomic(CONFIG_FILE, existing)

    print(f"\nSaved to {CONFIG_FILE}")
    print("Run 'homeops hvac status' to verify Prometheus connectivity.\n")
