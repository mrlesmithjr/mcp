"""CLI entry point - argparse setup, command routing, auto-log wiring."""

import argparse
import getpass
import json
import shutil
import subprocess
import sys
from datetime import datetime

from lawnops import advisory, db, irrigation, weather
from lawnops import config as config_mod
from lawnops import pollen as pollen_mod
from lawnops.cli import display
from lawnops.coverage import calculate_coverage
from lawnops.mixrate import calculate_mix
from lawnops.recommend import get_recommendation
from lawnops.window import find_application_window


def main():
    parser = argparse.ArgumentParser(
        prog="lawnops", description="Lawn care operations CLI - weather, irrigation, treatments, and cost tracking"
    )
    parser.add_argument("--no-log", action="store_true", help="Skip auto-logging to database")
    subparsers = parser.add_subparsers(dest="command")

    # --- configure ---
    subparsers.add_parser("configure", help="Set up Hydrawise credentials interactively")

    # Soil temp commands
    subparsers.add_parser("now", help="Current soil temp and threshold status")
    subparsers.add_parser("trend", help="14-day history with daily min/avg/max")
    subparsers.add_parser("advisory", help="Full report with pre-emergent recommendation")
    subparsers.add_parser("spray", help="Spray window check for next 48 hours")
    subparsers.add_parser("recommend", help="Fertilizer recommendation based on season and conditions")
    subparsers.add_parser("bermuda-check", help="Report soil temp status for Bermuda green-up")

    # Pollen command
    pollen_parser = subparsers.add_parser("pollen", help="Current pollen count and spray impact")
    pollen_parser.add_argument(
        "pollen_sub", nargs="?", choices=["trend"], help="Subcommand: trend (default: current count)"
    )
    pollen_parser.add_argument("--days", type=int, default=7, help="Days for trend (default: 7)")

    # Application window finder
    window_parser = subparsers.add_parser("window", help="Find best application window for next 7 days")
    window_parser.add_argument("type", choices=["spray", "granular", "pre-emergent"], help="Product application type")

    # Coverage calculator
    cov_parser = subparsers.add_parser("coverage", help="Calculate product coverage for yard")
    cov_parser.add_argument("product", help="Product name (fuzzy match)")
    cov_parser.add_argument("--sqft", type=int, help="Override yard sq ft")

    # Mix rate calculator
    mix_parser = subparsers.add_parser("mix", help="Calculate spray mix rate")
    mix_parser.add_argument("product", help="Product name (fuzzy match)")
    mix_parser.add_argument("--tank", type=float, default=4.0, help="Tank size in gallons (default: 4)")
    mix_parser.add_argument("--rate", choices=["southern", "northern"], help="Rate type (default: southern)")

    # Irrigation subcommand
    irr_parser = subparsers.add_parser("irrigation", aliases=["irr"], help="Hydrawise irrigation control")
    irr_sub = irr_parser.add_subparsers(dest="irr_command")

    irr_sub.add_parser("status", help="Show controller and zone status")

    run_parser = irr_sub.add_parser("run", help="Run a zone manually")
    run_parser.add_argument("zone", type=int, help="Zone number (1-8)")
    run_parser.add_argument("minutes", type=int, help="Duration in minutes")

    runall_parser = irr_sub.add_parser("runall", help="Run all/specific zones sequentially")
    runall_parser.add_argument("minutes", type=int, help="Duration per zone in minutes")
    runall_parser.add_argument("--zones", type=int, nargs="+", help="Specific zones (e.g. --zones 1 3 4 5)")

    stop_parser = irr_sub.add_parser("stop", help="Stop a zone or all zones")
    stop_parser.add_argument("zone", type=int, nargs="?", help="Zone number (omit for all)")

    suspend_parser = irr_sub.add_parser("suspend", help="Suspend zone(s)")
    suspend_parser.add_argument("hours", type=int, help="Hours to suspend")
    suspend_parser.add_argument("--zone", type=int, help="Specific zone (omit for all)")

    resume_parser = irr_sub.add_parser("resume", help="Resume suspended zone(s)")
    resume_parser.add_argument("--zone", type=int, help="Specific zone (omit for all)")

    history_parser = irr_sub.add_parser("history", help="Recent watering history")
    history_parser.add_argument("--days", type=int, default=7, help="Days of history (default: 7)")

    adjust_parser = irr_sub.add_parser("zone-adjust", help="Set per-zone watering adjustment %")
    adjust_parser.add_argument("zone", type=int, help="Zone number (1-8)")
    adjust_parser.add_argument("adjustment", type=int, help="Adjustment %% (100=normal, 110=10%% more, 80=20%% less)")

    export_parser = irr_sub.add_parser(
        "export", help="Export live controller config to YAML desired-state file (read-only)"
    )
    export_parser.add_argument(
        "--output",
        help="Output path for YAML file (default: ~/.config/lawnops/irrigation_state.yaml)",
    )

    diff_parser = irr_sub.add_parser(
        "diff", help="Compare live controller config against desired-state YAML file (read-only)"
    )
    diff_parser.add_argument(
        "--file",
        help="Path to desired-state YAML file (default: ~/.config/lawnops/irrigation_state.yaml)",
    )

    apply_parser = irr_sub.add_parser(
        "apply",
        help=("Converge live controller to desired-state YAML (DRY-RUN by default; use --confirm to write)"),
    )
    apply_parser.add_argument(
        "--file",
        help="Path to desired-state YAML file (default: ~/.config/lawnops/irrigation_state.yaml)",
    )
    apply_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Execute writes to the live controller (omit for dry-run preview)",
    )

    irr_sub.add_parser(
        "check",
        help="Report irrigation budget/ET issues",
    )

    # Database subcommand
    db_parser = subparsers.add_parser("db", help="Database management and queries")
    db_sub = db_parser.add_subparsers(dest="db_command")

    db_sub.add_parser("init", help="Initialize the database")

    # db treatment
    treat_parser = db_sub.add_parser("treatment", help="Treatment tracking")
    treat_sub = treat_parser.add_subparsers(dest="treat_command")

    treat_add = treat_sub.add_parser("add", help="Add a treatment")
    treat_add.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    treat_add.add_argument("--area", required=True, help="Treatment area")
    treat_add.add_argument("--product", required=True, help="Product used")
    treat_add.add_argument("--method", help="Application method")
    treat_add.add_argument("--amount", help="Amount applied")
    treat_add.add_argument("--soil-temp", type=float, dest="soil_temp", help="Soil temp at application")
    treat_add.add_argument("--cost", type=float, help="Cost")
    treat_add.add_argument("--notes", help="Notes")

    treat_list = treat_sub.add_parser("list", help="List treatments")
    treat_list.add_argument("--year", type=int, help="Year to filter (default: current)")

    # db product
    prod_parser = db_sub.add_parser("product", help="Product inventory")
    prod_sub = prod_parser.add_subparsers(dest="prod_command")

    prod_sub.add_parser("list", help="List products")

    prod_add = prod_sub.add_parser("add", help="Add a product")
    prod_add.add_argument("name", help="Product name")
    prod_add.add_argument("--category", help="Category (e.g. herbicide, fertilizer, insecticide)")
    prod_add.add_argument("--qty", type=float, default=1, help="Quantity on hand (default: 1)")
    prod_add.add_argument("--unit", default="bag", help="Unit (bag, bottle, etc.; default: bag)")
    prod_add.add_argument("--cost", type=float, help="Cost per unit")
    prod_add.add_argument("--source", help="Where purchased")
    prod_add.add_argument("--notes", help="Notes")

    prod_sub.add_parser("alerts", help="Show reorder alerts")

    prod_update = prod_sub.add_parser("update", help="Update product stock")
    prod_update.add_argument("name", help="Product name (partial match)")
    prod_update.add_argument("--qty", type=float, help="New quantity")
    prod_update.add_argument("--cost", type=float, help="New cost")

    # db purchase
    purch_parser = db_sub.add_parser("purchase", help="Purchase tracking")
    purch_sub = purch_parser.add_subparsers(dest="purch_command")

    purch_add = purch_sub.add_parser("add", help="Add a purchase")
    purch_add.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    purch_add.add_argument("--item", required=True, help="Item purchased")
    purch_add.add_argument("--category", help="Category (product, equipment, service, supplies)")
    purch_add.add_argument("--qty", type=float, default=1, help="Quantity")
    purch_add.add_argument("--cost", type=float, required=True, help="Total cost")
    purch_add.add_argument("--source", help="Where purchased")
    purch_add.add_argument("--notes", help="Notes")

    # db mowing
    mow_parser = db_sub.add_parser("mowing", help="Mowing service tracking")
    mow_sub = mow_parser.add_subparsers(dest="mow_command")

    mow_add = mow_sub.add_parser("add", help="Log a mowing visit")
    mow_add.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    mow_add.add_argument("--cost", type=float, help="Cost")
    mow_add.add_argument("--provider", help="Provider name (default: from config)")
    mow_add.add_argument("--notes", help="Notes")

    mow_summary = mow_sub.add_parser("summary", help="Mowing season summary")
    mow_summary.add_argument("--year", type=int, help="Year (default: current)")

    # db equipment
    equip_parser = db_sub.add_parser("equipment", help="Equipment inventory")
    equip_sub = equip_parser.add_subparsers(dest="equip_command")
    equip_sub.add_parser("list", help="List equipment")

    # db report
    report_parser = db_sub.add_parser("report", help="Reports and summaries")
    report_sub = report_parser.add_subparsers(dest="report_command")

    spend_parser = report_sub.add_parser("spend", help="Spending summary")
    spend_parser.add_argument("--year", type=int, help="Year (default: current)")
    spend_parser.add_argument("--category", help="Filter by category")

    water_parser = report_sub.add_parser("water-usage", help="Irrigation vs water bill correlation")
    water_parser.add_argument("--year", type=int, help="Year (default: all available)")

    report_sub.add_parser("pace", help="Current month irrigation pace vs historical averages")

    report_sub.add_parser("budget", help="Irrigation budget tracking (minutes or dollars)")

    zones_parser = report_sub.add_parser("zones", help="Per-zone runtime breakdown with outlier detection")
    zones_parser.add_argument("--year", type=int, help="Year (default: all time)")
    zones_parser.add_argument("--month", type=int, help="Month 1-12 (requires --year)")

    et_parser = report_sub.add_parser("et", help="ET% recommendations based on cost analysis")
    et_parser.add_argument("--year", type=int, help="Year to analyze (default: previous year)")

    # db sync-irrigation
    sync_irr = db_sub.add_parser("sync-irrigation", help="Sync Hydrawise history to DB")
    sync_irr.add_argument("--days", type=int, default=7, help="Days to sync (default: 7)")

    # db backfill-irrigation
    db_sub.add_parser("backfill-irrigation", help="Pull max Hydrawise history (~1 year) into DB")

    # db import-obsidian
    db_sub.add_parser("import-obsidian", help="One-time import from Task List.md")

    # db import-ynab
    ynab_parser = db_sub.add_parser("import-ynab", help="Import lawn spending from ynab-tools database")
    ynab_parser.add_argument("--year", type=int, help="Year to import (default: all)")
    ynab_parser.add_argument("--preview", action="store_true", help="Preview what would be imported without writing")

    args = parser.parse_args()

    # Default to 'now' if no command given
    if args.command is None:
        args.command = "now"

    if args.command == "configure":
        _cmd_configure()
        return

    try:
        config = config_mod.load_config()
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    # Respect --no-log flag
    if getattr(args, "no_log", False):
        config.setdefault("database", {})["auto_log"] = False

    # ── Database commands ──
    if args.command == "db":
        _handle_db(
            args, config, db_parser, treat_parser, prod_parser, purch_parser, mow_parser, equip_parser, report_parser
        )
        return

    # ── Irrigation commands ──
    if args.command in ("irrigation", "irr"):
        _handle_irrigation(args, config)
        return

    # ── Pollen command (no weather fetch needed) ──
    if args.command == "pollen":
        pollen_data = pollen_mod.fetch_pollen(config)
        if getattr(args, "pollen_sub", None) == "trend":
            history = db.get_pollen_history(config, getattr(args, "days", 7))
            display.display_pollen_trend(history, getattr(args, "days", 7))
        else:
            if pollen_data is None:
                print("Pollen data unavailable (off-season or fetch error).")
            else:
                db.log_pollen(config, pollen_data)
                display.display_pollen(pollen_data, config)
        return

    # ── Coverage calculator (no weather fetch needed) ──
    if args.command == "coverage":
        try:
            result = calculate_coverage(args.product, config, sqft_override=getattr(args, "sqft", None))
            display.display_coverage(result)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # ── Mix rate calculator (no weather fetch needed) ──
    if args.command == "mix":
        try:
            result = calculate_mix(
                args.product, getattr(args, "tank", 4.0), config, rate_type=getattr(args, "rate", None)
            )
            display.display_mix(result)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # ── Soil temp commands - fetch weather data ──
    try:
        data = weather.fetch_data(config)
    except Exception as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        sys.exit(1)

    daily_data = weather.aggregate_daily(data)
    current = weather.get_current(data)

    # Auto-log daily observations
    advisory_level = None
    if args.command == "advisory":
        advisory_level, _ = advisory.pre_emergent_advisory(daily_data, config)
    db.log_daily_observations(config, daily_data, advisory_level)

    # Fetch pollen data for commands that use it (non-blocking)
    pollen_data = None
    if args.command in ("now", "spray"):
        pollen_data = pollen_mod.fetch_pollen(config)
        db.log_pollen(config, pollen_data)

    if args.command == "now":
        display.display_now(current, daily_data, config, pollen_data=pollen_data)
    elif args.command == "trend":
        display.display_trend(daily_data, config)
    elif args.command == "advisory":
        display.display_advisory(current, daily_data, config)
    elif args.command == "spray":
        result = advisory.spray_advisory(daily_data, data, config, pollen_data=pollen_data)
        display.display_spray(result, config)
    elif args.command == "recommend":
        result = get_recommendation(config, daily_data)
        display.display_recommend(result, config)
    elif args.command == "bermuda-check":
        _cmd_bermuda_check(current, config)
    elif args.command == "window":
        result = find_application_window(args.type, daily_data, config)
        display.display_window(result, args.type, config)


def _handle_db(
    args, config, db_parser, treat_parser, prod_parser, purch_parser, mow_parser, equip_parser, report_parser
):
    """Route database subcommands."""
    db_cmd = getattr(args, "db_command", None)
    if db_cmd is None:
        db_parser.print_help()
        return

    try:
        if db_cmd == "init":
            db_path = db.init_db(config)
            print(f"Database initialized at {db_path}")

        elif db_cmd == "treatment":
            sub = getattr(args, "treat_command", None)
            if sub == "add":
                db.add_treatment(
                    config,
                    args.date,
                    args.area,
                    args.product,
                    args.method,
                    args.amount,
                    args.soil_temp,
                    args.cost,
                    args.notes,
                )
                print(f"Treatment added: {args.product} on {args.date}")
            elif sub == "list":
                rows, year = db.list_treatments(config, getattr(args, "year", None))
                display.display_treatments(rows, year)
            else:
                treat_parser.print_help()

        elif db_cmd == "product":
            sub = getattr(args, "prod_command", None)
            if sub == "list":
                rows = db.list_products(config)
                display.display_products(rows)
            elif sub == "add":
                db.add_product(
                    config,
                    args.name,
                    args.category,
                    args.qty,
                    args.unit,
                    args.cost,
                    args.source,
                    getattr(args, "notes", None),
                )
                print(f"Product added: {args.name}")
            elif sub == "update":
                rowcount = db.update_product(config, args.name, args.qty, args.cost)
                if rowcount == 0:
                    print(f"No product found matching '{args.name}'")
                else:
                    print(f"Updated {rowcount} product(s) matching '{args.name}'")
            elif sub == "alerts":
                alerts = db.get_reorder_alerts(config)
                display.display_reorder_alerts(alerts)
            else:
                prod_parser.print_help()

        elif db_cmd == "purchase":
            sub = getattr(args, "purch_command", None)
            if sub == "add":
                db.add_purchase(
                    config, args.date, args.item, args.category, args.qty, args.cost, args.source, args.notes
                )
                print(f"Purchase added: {args.item} on {args.date} (${args.cost:.2f})")
            else:
                purch_parser.print_help()

        elif db_cmd == "mowing":
            sub = getattr(args, "mow_command", None)
            if sub == "add":
                db.add_mowing(config, args.date, args.provider, args.cost, args.notes)
                print(f"Mowing visit logged: {args.date} by {args.provider}")
            elif sub == "summary":
                rows, total_visits, total_cost, year = db.get_mowing_summary(config, getattr(args, "year", None))
                display.display_mowing_summary(rows, total_visits, total_cost, year)
            else:
                mow_parser.print_help()

        elif db_cmd == "equipment":
            sub = getattr(args, "equip_command", None)
            if sub == "list":
                rows = db.list_equipment(config)
                display.display_equipment(rows)
            else:
                equip_parser.print_help()

        elif db_cmd == "report":
            sub = getattr(args, "report_command", None)
            if sub == "spend":
                cat_rows, grand_total, item_rows, year = db.get_spend_report(
                    config, getattr(args, "year", None), getattr(args, "category", None)
                )
                display.display_spend_report(cat_rows, grand_total, item_rows, year, getattr(args, "category", None))
            elif sub == "water-usage":
                result = db.get_water_usage_report(config, getattr(args, "year", None))
                display.display_water_usage(result)
            elif sub == "pace":
                result = db.irrigation_pace(config)
                display.display_irrigation_pace(result)
            elif sub == "budget":
                result = db.irrigation_budget(config)
                display.display_irrigation_budget(result)
            elif sub == "zones":
                result = db.zone_analysis(config, getattr(args, "year", None), getattr(args, "month", None))
                display.display_zone_analysis(result)
            elif sub == "et":
                result = db.et_recommendations(config, getattr(args, "year", None))
                display.display_et_recommendations(result)
            else:
                report_parser.print_help()

        elif db_cmd == "sync-irrigation":
            count, days = db.sync_irrigation(config, args.days)
            print(f"Synced {count} irrigation runs from last {days} days")

        elif db_cmd == "backfill-irrigation":
            print("Backfilling irrigation history from Hydrawise (~1 year)...")
            count, days = db.backfill_irrigation(config)
            print(f"Backfilled {count} irrigation runs from last {days} days")

        elif db_cmd == "import-obsidian":
            counts = db.import_from_obsidian(config)
            display.display_obsidian_import(counts)

        elif db_cmd == "import-ynab":
            year = getattr(args, "year", None)
            if getattr(args, "preview", False):
                mowing, purchases, skipped = db.preview_ynab_import(config, year)
                display.display_ynab_preview(mowing, purchases, skipped)
            else:
                mowing_count, purchase_count, skip_count = db.import_from_ynab(config, year)
                display.display_ynab_import(mowing_count, purchase_count, skip_count)

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _handle_irrigation(args, config):
    """Route irrigation subcommands."""
    irr_cmd = getattr(args, "irr_command", None) or "status"

    try:
        if irr_cmd == "status":
            ctrl, sensors, programs = irrigation.get_status()
            display.display_irrigation_status(ctrl, sensors, programs)

        elif irr_cmd == "run":
            zone_name, zone_num, minutes = irrigation.run_zone(args.zone, args.minutes)
            display.display_irrigation_run(zone_name, zone_num, minutes)
            # Auto-log manual run
            now = datetime.now()
            db.log_irrigation_run(
                config,
                now.strftime("%Y-%m-%d"),
                zone_num,
                zone_name,
                now.strftime("%H:%M"),
                minutes,
                "started",
                source="manual-cli",
            )

        elif irr_cmd == "runall":
            queued = irrigation.run_all(args.minutes, args.zones)
            display.display_irrigation_runall(queued, args.minutes)
            # Auto-log manual runs
            now = datetime.now()
            for zone_num, zone_name in queued:
                db.log_irrigation_run(
                    config,
                    now.strftime("%Y-%m-%d"),
                    zone_num,
                    zone_name,
                    now.strftime("%H:%M"),
                    args.minutes,
                    "queued",
                    source="manual-cli",
                )

        elif irr_cmd == "stop":
            zone_name = irrigation.stop(args.zone)
            display.display_irrigation_stop(zone_name, args.zone)

        elif irr_cmd == "suspend":
            until_str, zone_name = irrigation.suspend(args.hours, args.zone)
            display.display_irrigation_suspend(until_str, zone_name, args.zone)

        elif irr_cmd == "resume":
            zone_name = irrigation.resume(args.zone)
            display.display_irrigation_resume(zone_name, args.zone)

        elif irr_cmd == "history":
            entries = irrigation.get_history(args.days)
            display.display_irrigation_history(entries, args.days)
            # Auto-log to database
            for e in entries:
                if "log_date" in e:
                    db.log_irrigation_run(
                        config,
                        e["log_date"],
                        e["zone_num"],
                        e["zone_name"],
                        e["log_start_time"],
                        e["log_duration_min"],
                        str(e["status"]),
                    )

        elif irr_cmd == "zone-adjust":
            result = irrigation.update_zone_settings(args.zone, args.adjustment)
            print(
                f"Zone {result['zone_num']} ({result['zone_name']}): "
                f"{result['old_adjustment']}% -> {result['new_adjustment']}%"
            )

        elif irr_cmd == "export":
            from lawnops.irrigation_config import export_config, write_yaml

            output_path = getattr(args, "output", None)
            ctrl, programs = irrigation.get_programs()
            cfg = export_config(ctrl, programs)
            written = write_yaml(cfg, output_path)
            n_prog = len(cfg["programs"])
            n_zone = len(cfg["zones_catalog"])
            print(f"Exported {n_prog} programs, {n_zone} zones -> {written}")

        elif irr_cmd == "diff":
            from lawnops.irrigation_config import diff_config, export_config, read_yaml

            file_path = getattr(args, "file", None)
            desired = read_yaml(file_path)
            ctrl, programs = irrigation.get_programs()
            live = export_config(ctrl, programs)
            result = diff_config(live, desired)

            if not result["has_drift"]:
                print("No drift detected. Live config matches desired state.")
            else:
                print(
                    f"Drift detected: {result['program_level_count']} program-level "
                    f"(actionable), {result['zone_level_count']} zone-level (advisory)."
                )
                for change in result["changes"]:
                    category = change["category"]
                    path = change["path"]
                    live_val = change.get("live")
                    desired_val = change.get("desired")
                    action = change.get("action", "changed")
                    advisory_note = " [advisory/ISE-blocked]" if change.get("advisory") else ""
                    print(f"  [{category}]{advisory_note} {path}: {action}")
                    if action == "changed":
                        print(f"    live:    {live_val}")
                        print(f"    desired: {desired_val}")
                sys.exit(1)

        elif irr_cmd == "apply":
            from lawnops.irrigation_config import diff_config, export_config, plan_apply, read_yaml

            file_path = getattr(args, "file", None)
            confirm = getattr(args, "confirm", False)

            desired = read_yaml(file_path)
            ctrl, programs = irrigation.get_programs()
            live = export_config(ctrl, programs)
            diff_result = diff_config(live, desired)
            apply_plan = plan_apply(diff_result, desired)

            if not diff_result["has_drift"]:
                print("No drift detected. Live config matches desired state. Nothing to apply.")
                return

            n_updates = len(apply_plan["updates"])
            n_skipped = len(apply_plan["skipped"])

            if not confirm:
                print(
                    f"DRY-RUN: {n_updates} program update(s) planned, "
                    f"{n_skipped} change(s) skipped. Pass --confirm to execute."
                )
                print()
                for upd in apply_plan["updates"]:
                    print(f"  [WOULD APPLY] program {upd['program_id']} ({upd['program_name']})")
                    print(f"    fields: {', '.join(upd['fields_applied'])}")
                for sk in apply_plan["skipped"]:
                    print(f"  [SKIP] {sk['path']}: {sk['reason']}")
                return

            # --confirm: execute writes
            print(f"Applying {n_updates} program update(s)...")
            report = irrigation.execute_apply(apply_plan)

            for applied in report["applied"]:
                print(
                    f"  [APPLIED] program {applied['program_id']} ({applied['program_name']}): "
                    f"{', '.join(applied['fields_applied'])}"
                )
            for sk in report["skipped"]:
                print(f"  [SKIPPED] {sk['path']}: {sk['reason']}")
            for err in report["errors"]:
                print(
                    f"  [ERROR] program {err['program_id']} ({err['program_name']}): {err['error']}",
                    file=sys.stderr,
                )

            if report["errors"]:
                print(
                    f"\nApply completed with {len(report['errors'])} error(s). "
                    f"{len(report['applied'])} succeeded, {len(report['skipped'])} skipped.",
                    file=sys.stderr,
                )
                sys.exit(1)
            else:
                print(
                    f"\nApply complete: {len(report['applied'])} applied, {len(report['skipped'])} skipped, 0 errors."
                )

        elif irr_cmd == "check":
            _cmd_irrigation_check(config)

    except (RuntimeError, OSError) as e:
        # OSError covers PermissionError / FileNotFoundError from export --output
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_irrigation_check(config):
    """Deterministic irrigation check: reuses budget_status/et_recommendations
    to flag budget/ET issues (issue #146). Read-only report: prints the
    findings (issue #39 - Apple Reminders creation removed, no delivery
    channel left).
    """
    result = db.evaluate_irrigation_check(config)
    budget_issue = result["budget_issue"]
    et_issue = result["et_issue"]

    if not result["qualifies"]:
        print("Irrigation check: no qualifying issues")
        return

    if budget_issue and et_issue:
        summary = "budget + ET"
    elif budget_issue:
        summary = f"budget {budget_issue['budget_status']}"
    else:
        summary = "ET reduction recommended"

    lines = []
    if budget_issue:
        lines.append(
            f"Budget {budget_issue['budget_status']}: ${budget_issue['current_cost']:.2f} spent, "
            f"${budget_issue['projected_cost']:.2f} projected"
        )
        for rec in budget_issue.get("recommendations", []):
            lines.append(f"- {rec}")
    if et_issue:
        for item in et_issue:
            lines.append(item["detail"])

    print(f"Irrigation check: {summary}")
    for line in lines:
        print(f"  {line}")


_BERMUDA_CHECKLIST = """\
- Mix rate: 2.5 oz Ortho Weed B-Gon + Crabgrass Control per gallon
- Spot treat only: do not broad spray dormant/transitioning Bermuda
- Spray in sections so dogs have access to untreated areas
- Log each treatment in lawnops after spraying
- Resume full schedule once grass is fully green"""


def _cmd_bermuda_check(current, config):
    """Deterministic Bermuda green-up check: soil temp vs threshold (issue
    #146). Read-only report: prints the checklist when ready (issue #39 -
    Apple Reminders creation removed, no delivery channel left).
    """
    soil_temp = current["soil_temp"]

    if not advisory.bermuda_greenup_ready(soil_temp, config):
        print(f"Bermuda green-up check: not ready yet (soil temp {soil_temp:.1f}°F)")
        return

    print(f"Bermuda green-up check: ready (soil temp {soil_temp:.1f}°F)")
    print(_BERMUDA_CHECKLIST)


def _cmd_configure():
    """Interactive setup wizard - writes Hydrawise credentials to ~/.config/lawnops/config.json."""
    from lawnops.atomic_io import atomic_write_json
    from lawnops.config import CONFIG_DIR, CONFIG_FILE

    print("\nLawnOps - Configuration Setup")
    print("=" * 40)
    print("Hydrawise credentials are required for irrigation control.")
    print("Weather and advisory features work without them.\n")

    sources = [("1", "Enter manually")]
    if shutil.which("op"):
        sources.append((str(len(sources) + 1), "1Password (op detected)"))

    if len(sources) == 1:
        source = "manual"
    else:
        print("Source credentials from:")
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
        api_key = _fetch_1password("Hydrawise", "api_key", "API key")
        username = _fetch_1password("Hydrawise", "username", "Username (email)")
        password = _fetch_1password("Hydrawise", "password", "Password")
    else:
        api_key = getpass.getpass("Hydrawise API key: ").strip()
        username = input("Username (email): ").strip()
        password = getpass.getpass("Password: ").strip()

    if not api_key or not username or not password:
        print("\nError: all three Hydrawise fields are required.", file=sys.stderr)
        sys.exit(1)

    # Patch only the hydrawise section; preserve all other existing config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    hydrawise = existing.get("hydrawise", {})
    hydrawise.update({"api_key": api_key, "username": username, "password": password})
    existing["hydrawise"] = hydrawise

    atomic_write_json(CONFIG_FILE, existing, mode=0o600, dir_mode=0o700)

    print(f"\nSaved to {CONFIG_FILE}")
    print("Run 'lawnops irrigation status' to verify Hydrawise connectivity.\n")


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
        print(f"Empty value returned for '{field_name}', enter manually.")
    except subprocess.CalledProcessError as e:
        print(f"1Password error: {e.stderr.strip()}")
        print("Enter manually.")
    is_secret = any(w in label.lower() for w in ("key", "password", "token"))
    return getpass.getpass(f"{label}: ").strip() if is_secret else input(f"{label}: ").strip()
