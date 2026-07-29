"""CLI entry point - argparse setup and command routing."""

import argparse
import getpass
import json
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="nextdns-tools",
        description="NextDNS operations - analytics, blocking stats, device activity, profile management",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- configure ---
    subparsers.add_parser("configure", help="Set up credentials interactively")

    # --- status ---
    status_parser = subparsers.add_parser("status", help="Query status breakdown")
    status_parser.add_argument("--hours", type=int, default=24, help="Hours to analyze (default: 24)")

    # --- blocked ---
    blocked_parser = subparsers.add_parser("blocked", help="Top blocked domains")
    blocked_parser.add_argument("--hours", type=int, default=24, help="Hours to analyze (default: 24)")

    # --- devices ---
    devices_parser = subparsers.add_parser("devices", help="Device activity")
    devices_parser.add_argument("--hours", type=int, default=24, help="Hours to analyze (default: 24)")

    # --- security ---
    security_parser = subparsers.add_parser("security", help="Security threat events")
    security_parser.add_argument("--hours", type=int, default=24, help="Hours to analyze (default: 24)")

    # --- logs ---
    logs_parser = subparsers.add_parser("logs", help="Recent DNS query logs")
    logs_parser.add_argument("--limit", type=int, default=50, help="Number of entries (default: 50)")
    logs_parser.add_argument("--blocked", action="store_true", help="Show only blocked queries")

    # --- profile ---
    subparsers.add_parser("profile", help="Show profile configuration")

    # --- allowlist ---
    allow_parser = subparsers.add_parser("allowlist", help="Manage allowlist")
    allow_parser.add_argument("--add", metavar="DOMAIN", help="Add domain to allowlist")
    allow_parser.add_argument("--remove", metavar="DOMAIN", help="Remove domain from allowlist")

    # --- denylist ---
    deny_parser = subparsers.add_parser("denylist", help="Manage denylist")
    deny_parser.add_argument("--add", metavar="DOMAIN", help="Add domain to denylist")
    deny_parser.add_argument("--remove", metavar="DOMAIN", help="Remove domain from denylist")

    # --- export ---
    export_parser = subparsers.add_parser("export", help="Export profile configuration")
    export_parser.add_argument("--output", "-o", help="Output file path (default: stdout)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "configure":
            _cmd_configure()
            return

        from nextdns import api

        if args.command == "status":
            _print_status(api.get_status(args.hours))
        elif args.command == "blocked":
            _print_blocked(api.get_blocked(args.hours))
        elif args.command == "devices":
            _print_devices(api.get_devices(args.hours))
        elif args.command == "security":
            _print_security(api.get_security(args.hours))
        elif args.command == "logs":
            logs = api.get_logs(args.limit)
            if args.blocked:
                logs = [log for log in logs if log["status"] == "blocked"]
            _print_logs(logs)
        elif args.command == "profile":
            _print_profile(api.get_profile())
        elif args.command == "allowlist":
            if args.add:
                ok = api.add_allowlist(args.add)
                print(f"  {'Added' if ok else 'Failed'}: {args.add}")
            elif args.remove:
                ok = api.remove_allowlist(args.remove)
                print(f"  {'Removed' if ok else 'Failed'}: {args.remove}")
            else:
                _print_list("Allowlist", api.get_allowlist())
        elif args.command == "denylist":
            if args.add:
                ok = api.add_denylist(args.add)
                print(f"  {'Added' if ok else 'Failed'}: {args.add}")
            elif args.remove:
                ok = api.remove_denylist(args.remove)
                print(f"  {'Removed' if ok else 'Failed'}: {args.remove}")
            else:
                _print_list("Denylist", api.get_denylist())
        elif args.command == "export":
            data = api.export_profile()
            if args.output:
                with open(args.output, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"  Exported to {args.output}")
            else:
                print(json.dumps(data, indent=2))
        else:
            parser.print_help()

    except RuntimeError as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


def _cmd_configure():
    """Interactive setup wizard - writes credentials to ~/.config/nextdns-tools/config.json."""
    from nextdns.config import CONFIG_DIR, CONFIG_FILE

    print("\nNextDNS Tools - Configuration Setup")
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
        api_key = _fetch_1password("NextDNS", "api_key", "API key")
        profile_id = _fetch_1password("NextDNS", "credential", "Profile ID")
    else:
        api_key = getpass.getpass("NextDNS API key: ").strip()
        profile_id = input("Profile ID: ").strip()

    if not api_key or not profile_id:
        print("\nError: both fields are required.", file=sys.stderr)
        sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"api_key": api_key, "profile_id": profile_id}, f, indent=2)
        f.write("\n")
    CONFIG_FILE.chmod(0o600)

    print(f"\nSaved to {CONFIG_FILE}")
    print("Run 'nextdns-tools status' to verify.\n")


def _fetch_1password(default_item: str, default_field: str, label: str) -> str:
    """Pull a single field from a 1Password item. Falls back to manual on error."""
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

    return getpass.getpass(f"{label}: ").strip()


def _print_status(data):
    total = data["total_queries"]
    print(f"\nDNS Status (last {data['hours']}h)")
    print(f"  Total queries: {total:,}")
    print("-" * 40)
    for item in data["by_status"]:
        pct = item["count"] / total * 100 if total > 0 else 0
        print(f"  {item['status']:<15} {item['count']:>8,} ({pct:.1f}%)")
    print()


def _print_blocked(blocked):
    if not blocked:
        print("\nNo blocked domains.")
        return

    print(f"\nTop Blocked Domains ({len(blocked)})")
    print(f"{'Domain':<55} {'Queries':>8}")
    print("-" * 65)
    for b in blocked:
        print(f"  {b['domain']:<55} {b['count']:>8,}")
    print()


def _print_devices(devices):
    if not devices:
        print("\nNo device data.")
        return

    print(f"\nDevice Activity ({len(devices)} devices)")
    print(f"{'Device':<35} {'Queries':>10} {'%':>7}")
    print("-" * 55)
    for d in devices:
        print(f"  {d['name']:<35} {d['count']:>10,} {d['percentage']:>6.1f}%")
    print()


def _print_security(events):
    if not events:
        print("\nNo security events.")
        return

    print(f"\nSecurity Events ({len(events)})")
    print("-" * 50)
    for e in events:
        print(f"  {json.dumps(e, default=str)}")
    print()


def _print_logs(logs):
    if not logs:
        print("\nNo log entries.")
        return

    print(f"\n{'Time':<22} {'Domain':<45} {'Status':<10} {'Device'}")
    print("-" * 95)
    for log in logs:
        ts = log["timestamp"][:19].replace("T", " ") if log["timestamp"] else ""
        status = log["status"]
        if log["reasons"]:
            status = "BLOCKED"
        print(f"  {ts:<22} {log['domain']:<45} {status:<10} {log['device']}")
    print(f"\n  {len(logs)} entries\n")


def _print_profile(profile):
    sec = profile.get("security", {})
    priv = profile.get("privacy", {})
    blocklists = priv.get("blocklists", [])
    natives = priv.get("natives", [])
    settings = profile.get("settings", {})

    print("\nNextDNS Profile")
    print("=" * 50)

    print(f"\n  Security ({sum(1 for v in sec.values() if v is True)}/12 features)")
    for k, v in sorted(sec.items()):
        if isinstance(v, bool):
            print(f"    {'[x]' if v else '[ ]'} {k}")

    print(f"\n  Blocklists ({len(blocklists)})")
    for b in blocklists:
        print(f"    - {b.get('id', b)}")

    print(f"\n  Native Tracking ({len(natives)})")
    for n in natives:
        print(f"    - {n.get('id', n)}")

    print("\n  Privacy")
    print(f"    Disguised trackers: {'yes' if priv.get('disguisedTrackers') else 'no'}")
    print(f"    Allow affiliate:   {'yes' if priv.get('allowAffiliate') else 'no'}")

    logs = settings.get("logs", {})
    perf = settings.get("performance", {})
    print("\n  Settings")
    print(f"    Logs:              {'on' if logs.get('enabled') else 'off'} (location: {logs.get('location', '?')})")
    print(f"    Cache boost:       {'on' if perf.get('cacheBoost') else 'off'}")
    print(f"    ECS:               {'on' if perf.get('ecs') else 'off'}")
    print(f"    CNAME flattening:  {'on' if perf.get('cnameFlattening') else 'off'}")
    print(f"    BAV:               {'on' if settings.get('bav') else 'off'}")

    allowlist = profile.get("allowlist", [])
    denylist = profile.get("denylist", [])
    print(f"\n  Allowlist: {len(allowlist)} entries")
    print(f"  Denylist:  {len(denylist)} entries")
    print()


def _print_list(name, entries):
    if not entries:
        print(f"\n{name} is empty.")
        return

    print(f"\n{name} ({len(entries)} entries)")
    print("-" * 50)
    for e in sorted(entries):
        print(f"  {e}")
    print()
