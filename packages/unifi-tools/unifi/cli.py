"""CLI entry point - argparse setup and command routing."""

import argparse
import getpass
import json
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="unifi-tools",
        description="UniFi network operations - device inventory, client monitoring, and security status",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- configure ---
    subparsers.add_parser("configure", help="Set up credentials interactively")

    # --- devices ---
    subparsers.add_parser("devices", help="List all adopted UniFi devices")

    # --- clients ---
    client_parser = subparsers.add_parser("clients", help="List connected clients")
    client_parser.add_argument("--network", help="Filter by network/SSID name")
    client_parser.add_argument("--count", action="store_true", help="Show count summary only")

    # --- device-stats ---
    ds_parser = subparsers.add_parser(
        "device-stats", help="Per-device CPU, memory, and uplink stats (requires api_key)"
    )
    ds_parser.add_argument("--device", help="Filter by device name or MAC address")

    # --- device-restart ---
    dr_parser = subparsers.add_parser("device-restart", help="Restart a UniFi device (requires api_key)")
    dr_parser.add_argument("device", help="Device name or MAC address")
    dr_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # --- acl-rules ---
    subparsers.add_parser("acl-rules", help="List ACL rules by priority (requires api_key)")

    # --- traffic-matching-lists ---
    tml_parser = subparsers.add_parser(
        "traffic-matching-lists",
        help="List traffic matching lists for firewall rules (requires api_key)",
    )
    tml_parser.add_argument(
        "--no-items",
        action="store_true",
        help="Show summary only (id, type, name) without fetching list items",
    )

    # --- radio-list ---
    rl_parser = subparsers.add_parser("radio-list", help="List per-radio config for an access point (requires api_key)")
    rl_parser.add_argument("device", help="Device name or MAC address")

    # --- switch-ports ---
    sp_parser = subparsers.add_parser(
        "switch-ports", help="List switch ports with enabled and PoE status (requires api_key)"
    )
    sp_parser.add_argument("device", help="Device name or MAC address")

    # --- port-toggle ---
    pt_parser = subparsers.add_parser("port-toggle", help="Enable or disable a switch port (requires api_key)")
    pt_parser.add_argument("device", help="Device name or MAC address")
    pt_parser.add_argument("port_idx", type=int, help="Port index (portIdx from switch-ports output)")
    pt_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # --- port-poe ---
    pp_parser = subparsers.add_parser("port-poe", help="Set PoE state on a switch port (requires api_key)")
    pp_parser.add_argument("device", help="Device name or MAC address")
    pp_parser.add_argument("port_idx", type=int, help="Port index (portIdx from switch-ports output)")
    pp_parser.add_argument("state", choices=["on", "off"], help="PoE state to set")
    pp_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # --- firewall-policies ---
    subparsers.add_parser("firewall-policies", help="List firewall policies with zones and actions (requires api_key)")

    # --- vlans ---
    subparsers.add_parser("vlans", help="List VLAN/network configurations")

    # --- wlans ---
    subparsers.add_parser("wlans", help="List WiFi SSIDs")

    # --- vpn ---
    subparsers.add_parser("vpn", help="VPN server and site-to-site tunnel status (requires api_key)")

    # --- wan ---
    subparsers.add_parser("wan", help="WAN health status")

    # --- ips ---
    subparsers.add_parser("ips", help="IPS/IDS security status")

    # --- firewall ---
    subparsers.add_parser("firewall", help="Firewall zone assignments")

    # --- dns ---
    subparsers.add_parser("dns", help="DNS configuration")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "configure":
            _cmd_configure()
            return

        from unifi import api

        if args.command == "device-restart":
            d = api._find_v1_device(args.device)
            if not args.yes:
                confirm = (
                    input(f"\nRestart '{d['name']}' ({d['ip']})? This will interrupt connectivity. [y/N] ")
                    .strip()
                    .lower()
                )
                if confirm != "y":
                    print("Aborted.")
                    return
            api.restart_device(d["id"])
            print(f"\nRestart command sent to '{d['name']}' ({d['ip']}).")
            print("Device will be offline for ~30-90 seconds.\n")
        elif args.command == "device-stats":
            if args.device:
                d = api._find_v1_device(args.device)
                stats = api.get_device_stats(d["id"])
                _print_device_stats([{"name": d["name"], "model": d["model"], "ip": d["ip"], **stats}])
            else:
                _print_device_stats(api.get_all_device_stats())
        elif args.command == "devices":
            _print_devices(api.get_devices())
        elif args.command == "clients":
            if args.count:
                _print_client_count(api.get_clients())
            else:
                clients = api.get_clients()
                if args.network:
                    net = args.network.lower()
                    clients = [
                        c
                        for c in clients
                        if net in (c.get("network") or "").lower() or net in (c.get("essid") or "").lower()
                    ]
                _print_clients(clients)
        elif args.command == "radio-list":
            d = api._find_v1_device(args.device)
            radios = api.get_device_radios(d["id"])
            _print_radio_list(d["name"], radios)
        elif args.command == "switch-ports":
            d = api._find_v1_device(args.device)
            if "switching" not in d.get("features", []):
                print(
                    f"\nWarning: '{d['name']}' does not report 'switching' capability. Port data may be unavailable.\n"
                )
            ports = api.get_device_ports(d["id"])
            _print_switch_ports(d["name"], ports)
        elif args.command == "port-toggle":
            d = api._find_v1_device(args.device)
            ports = api.get_device_ports(d["id"])
            port = next((p for p in ports if p.get("portIdx") == args.port_idx), None)
            if port is None:
                raise RuntimeError(f"Port index {args.port_idx} not found on '{d['name']}'.")
            # Default True: if firmware omits the field, assume enabled and send disable.
            current = port.get("enabled", True)
            action = "disable" if current else "enable"
            port_name = port.get("name") or port.get("description") or f"port {args.port_idx}"
            if not args.yes:
                confirm = (
                    input(
                        f"\n{'Disable' if current else 'Enable'} port {args.port_idx} ({port_name}) on '{d['name']}'? [y/N] "
                    )
                    .strip()
                    .lower()
                )
                if confirm != "y":
                    print("Aborted.")
                    return
            api.port_action(d["id"], args.port_idx, action)
            print(f"\nPort {args.port_idx} ({port_name}) on '{d['name']}' {action}d.\n")
        elif args.command == "port-poe":
            d = api._find_v1_device(args.device)
            ports = api.get_device_ports(d["id"])
            port = next((p for p in ports if p.get("portIdx") == args.port_idx), None)
            if port is None:
                raise RuntimeError(f"Port index {args.port_idx} not found on '{d['name']}'.")
            action = "poe_enable" if args.state == "on" else "poe_disable"
            port_name = port.get("name") or port.get("description") or f"port {args.port_idx}"
            if not args.yes:
                confirm = (
                    input(
                        f"\nSet PoE {'on' if args.state == 'on' else 'off'} for port {args.port_idx} ({port_name}) on '{d['name']}'? [y/N] "
                    )
                    .strip()
                    .lower()
                )
                if confirm != "y":
                    print("Aborted.")
                    return
            api.port_action(d["id"], args.port_idx, action)
            print(
                f"\nPoE {'enabled' if args.state == 'on' else 'disabled'} on port {args.port_idx} ({port_name}) of '{d['name']}'.\n"
            )
        elif args.command == "acl-rules":
            _print_acl_rules(api.get_acl_rules())
        elif args.command == "traffic-matching-lists":
            _print_traffic_matching_lists(api.get_traffic_matching_lists(include_items=not args.no_items))
        elif args.command == "firewall-policies":
            _print_firewall_policies(api.get_firewall_policies())
        elif args.command == "vpn":
            _print_vpn(api.get_vpn_status())
        elif args.command == "vlans":
            _print_vlans(api.get_networks())
        elif args.command == "wlans":
            _print_wlans(api.get_wlans())
        elif args.command == "wan":
            _print_wan(api.get_wan_status())
        elif args.command == "ips":
            _print_ips(api.get_ips_settings())
        elif args.command == "firewall":
            _print_firewall(api.get_firewall_zones())
        elif args.command == "dns":
            _print_dns(api.get_dns_settings())
        else:
            parser.print_help()

    except RuntimeError as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


def _cmd_configure():
    """Interactive setup wizard - writes credentials to ~/.config/unifi-tools/config.json."""
    from unifi.config import CONFIG_DIR, CONFIG_FILE, _DEFAULTS

    print("\nUniFi Tools - Configuration Setup")
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
        host = _fetch_1password("UniFi", "host", "Controller host/IP")
        username = _fetch_1password("UniFi", "username", "Username")
        password = _fetch_1password("UniFi", "password", "Password")
    else:
        host = input("Controller host/IP (e.g. 192.168.1.1): ").strip()
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ").strip()

    if not host or not username or not password:
        print("\nError: host, username, and password are required.", file=sys.stderr)
        sys.exit(1)

    print("\nUniFi Network v1 API Key (optional)")
    print("  Generate in: UniFi application > Settings > Integrations")
    print("  Required for firewall policies, device stats, ACL rules, and more.")
    if source == "1password":
        api_key = _fetch_1password("UniFi", "api_key", "API Key (blank to skip)")
    else:
        api_key = input("API Key (blank to skip): ").strip()

    # Preserve non-sensitive defaults; only overwrite credential fields
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    config = {**_DEFAULTS, **existing, "host": host, "username": username, "password": password}
    if api_key:
        config["api_key"] = api_key
    elif existing.get("api_key"):
        config["api_key"] = existing["api_key"]
    config.pop("zone_map", None)  # keep zone_map only if already set
    if existing.get("zone_map"):
        config["zone_map"] = existing["zone_map"]

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    CONFIG_FILE.chmod(0o600)

    print(f"\nSaved to {CONFIG_FILE}")
    print("Run 'unifi-tools devices' to verify.\n")


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
    return getpass.getpass(f"{label}: ").strip() if "password" in label.lower() else input(f"{label}: ").strip()


def _format_uptime(seconds):
    """Format seconds to human-readable uptime."""
    if not seconds:
        return "-"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days > 0:
        return f"{days}d {hours}h"
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _format_bytes(b):
    """Format bytes to human-readable."""
    if not b:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _print_device_stats(devices):
    if not devices:
        print("\nNo device stats available.")
        return

    print(
        f"\n{'Name':<25} {'Model':<12} {'IP':<16} {'CPU%':<7} {'Mem%':<7} {'Load(1m)':<10} {'TX Mbps':<10} {'RX Mbps'}"
    )
    print("-" * 100)
    for d in devices:
        if "error" in d:
            print(f"  {d['name']:<25} {d.get('model', ''):<12} {d.get('ip', ''):<16} error: {d['error']}")
            continue
        cpu = f"{d.get('cpuUtilizationPct', 0):.1f}" if d.get("cpuUtilizationPct") is not None else "-"
        mem = f"{d.get('memoryUtilizationPct', 0):.1f}" if d.get("memoryUtilizationPct") is not None else "-"
        load = f"{d.get('loadAverage1Min', 0):.2f}" if d.get("loadAverage1Min") is not None else "-"
        uplink = d.get("uplink") or {}
        tx = f"{uplink.get('txRateBps', 0) / 1_000_000:.1f}" if uplink.get("txRateBps") is not None else "-"
        rx = f"{uplink.get('rxRateBps', 0) / 1_000_000:.1f}" if uplink.get("rxRateBps") is not None else "-"
        print(
            f"  {d.get('name', ''):<25} {d.get('model', ''):<12} {d.get('ip', ''):<16} {cpu:<7} {mem:<7} {load:<10} {tx:<10} {rx}"
        )
    print()


def _print_devices(devices):
    if not devices:
        print("\nNo devices found.")
        return

    print(f"\n{'Name':<25} {'Model':<12} {'IP':<16} {'Version':<12} {'Uptime':<10} {'Status'}")
    print("-" * 90)
    for d in devices:
        uptime = _format_uptime(d.get("uptime_seconds"))
        status = "OK" if d.get("state") == 1 else "offline"
        if d.get("upgradable"):
            status += " (upgrade avail)"
        print(f"  {d['name']:<25} {d['model']:<12} {d['ip']:<16} {d['version']:<12} {uptime:<10} {status}")
    print()


def _print_clients(clients):
    if not clients:
        print("\nNo clients connected.")
        return

    print(f"\n{'Name':<30} {'IP':<16} {'Network':<15} {'Type':<8} {'Signal':<8} {'Uptime'}")
    print("-" * 90)
    for c in clients:
        conn_type = "wired" if c.get("is_wired") else "wifi"
        signal = f"{c['signal']}dB" if c.get("signal") is not None else "-"
        net = c.get("network") or c.get("essid") or "-"
        uptime = _format_uptime(c.get("uptime_seconds"))
        print(f"  {c['name']:<30} {c['ip']:<16} {net:<15} {conn_type:<8} {signal:<8} {uptime}")
    print(f"\n  Total: {len(clients)} clients\n")


def _print_client_count(clients):
    wired = sum(1 for c in clients if c.get("is_wired"))
    wireless = len(clients) - wired
    by_network = {}
    for c in clients:
        net = c.get("network") or c.get("essid") or "unknown"
        by_network[net] = by_network.get(net, 0) + 1

    print(f"\nConnected Clients: {len(clients)} ({wired} wired, {wireless} wireless)")
    print("-" * 40)
    for net, count in sorted(by_network.items(), key=lambda x: -x[1]):
        print(f"  {net:<25} {count}")
    print()


def _print_traffic_matching_lists(lists):
    if not lists:
        print("\nNo traffic matching lists configured.")
        return

    print(f"\nTraffic Matching Lists ({len(lists)})")
    print("-" * 60)
    for lst in lists:
        print(f"  [{lst.get('type', '-')}] {lst.get('name', '-')}  ({lst.get('id', '-')})")
        if "error" in lst:
            print(f"    error: {lst['error']}")
            continue
        items = lst.get("items") or []
        if not items:
            print("    (no items)")
            continue
        for item in items:
            if not isinstance(item, dict):
                print(f"    {item}")
                continue
            itype = item.get("type", "")
            if itype in ("ADDRESS", "SUBNET"):
                print(f"    {item.get('value', '-')}")
            elif itype == "ADDRESS_RANGE":
                print(f"    {item.get('start', '-')} - {item.get('stop', '-')}")
            elif itype == "NUMBER":
                print(f"    {item.get('value', '-')}")
            elif itype == "NUMBER_RANGE":
                print(f"    {item.get('start', '-')} - {item.get('stop', '-')}")
            else:
                print(f"    [{itype}] {item}")
    print()


def _print_radio_list(device_name, radios):
    if not radios:
        print(
            f"\nNo radio data returned for '{device_name}'. Device may not expose interfaces.radios in this firmware.\n"
        )
        return

    print(f"\nRadio Config - {device_name} ({len(radios)} radio(s))\n")
    for i, r in enumerate(radios):
        print(f"  Radio {i}:")
        for key, val in r.items():
            print(f"    {key}: {val}")
        print()


def _print_switch_ports(device_name, ports):
    if not ports:
        print(
            f"\nNo port data returned for '{device_name}'. Device may not support the ports endpoint on this firmware.\n"
        )
        return

    print(f"\nSwitch Ports - {device_name} ({len(ports)} ports)")
    print(f"\n{'Idx':<5} {'Name':<25} {'Enabled':<8} {'PoE'}")
    print("-" * 55)
    for p in sorted(ports, key=lambda x: x.get("portIdx", 0)):
        idx = str(p.get("portIdx", "-"))
        name = p.get("name") or p.get("description") or "-"
        enabled = "yes" if p.get("enabled", True) else "no"
        # poeMode varies by firmware: "auto", "off", True/False, or absent
        poe_raw = p.get("poeMode", p.get("poe", None))
        if poe_raw is None:
            poe = "-"
        elif isinstance(poe_raw, bool):
            poe = "on" if poe_raw else "off"
        else:
            poe = str(poe_raw)
        print(f"  {idx:<5} {name:<25} {enabled:<8} {poe}")
    print()


def _print_acl_rules(rules):
    if not rules:
        print("\nNo ACL rules configured.")
        return

    print(f"\n{'#':<5} {'Name':<30} {'Type':<6} {'Action':<7} {'Enabled':<8} {'Source':<30} {'Destination'}")
    print("-" * 110)
    for r in rules:
        enabled = "yes" if r.get("enabled") else "no"
        action = r.get("action") or "-"
        rtype = r.get("type") or "-"
        idx = str(r.get("index", "-"))

        def _describe_filter(f):
            if not f:
                return "any"
            ftype = f.get("type", "")
            if ftype == "IP_ADDRESSES_OR_SUBNETS":
                addrs = f.get("ipAddressesOrSubnets", [])
                return ", ".join(addrs[:2]) + ("..." if len(addrs) > 2 else "")
            if ftype == "NETWORKS":
                ids = f.get("networkIds", [])
                return f"{len(ids)} network(s)"
            if ftype == "PORTS":
                ports = f.get("portFilter", [])
                return "ports:" + ",".join(str(p) for p in ports[:3]) + ("..." if len(ports) > 3 else "")
            if ftype == "MAC_ADDRESSES":
                macs = f.get("macAddresses", [])
                return ", ".join(macs[:2]) + ("..." if len(macs) > 2 else "")
            return ftype or "any"

        src = _describe_filter(r.get("source_filter"))
        dst = _describe_filter(r.get("destination_filter"))
        print(f"  {idx:<5} {r['name']:<30} {rtype:<6} {action:<7} {enabled:<8} {src:<30} {dst}")
    print()


def _print_firewall_policies(policies):
    if not policies:
        print("\nNo firewall policies configured.")
        return

    print(f"\n{'#':<4} {'Name':<30} {'Action':<8} {'Enabled':<8} {'Src Zone':<36} {'Dst Zone'}")
    print("-" * 105)
    for p in sorted(policies, key=lambda x: x.get("index", 0)):
        enabled = "yes" if p.get("enabled") else "no"
        src = p.get("source_zone_id") or "-"
        dst = p.get("destination_zone_id") or "-"
        action = p.get("action") or "-"
        idx = str(p.get("index", "-"))
        print(f"  {idx:<4} {p['name']:<30} {action:<8} {enabled:<8} {src:<36} {dst}")
    print()


def _print_vpn(data):
    servers = data.get("servers", [])
    tunnels = data.get("tunnels", [])

    print(f"\nVPN Servers ({len(servers)})")
    print("-" * 60)
    if servers:
        for s in servers:
            enabled = "enabled" if s.get("enabled") else "disabled"
            print(f"  [{s.get('type', '-')}] {s.get('name', '-')}  ({enabled})")
    else:
        print("  None configured.")

    print(f"\nSite-to-Site Tunnels ({len(tunnels)})")
    print("-" * 60)
    if tunnels:
        for t in tunnels:
            print(f"  [{t.get('type', '-')}] {t.get('name', '-')}")
    else:
        print("  None configured.")
    print()


def _print_vlans(networks):
    if not networks:
        print("\nNo networks configured.")
        return

    print(f"\n{'Name':<30} {'VLAN':<6} {'Subnet':<20} {'DHCP':<6} {'Enabled'}")
    print("-" * 75)
    for n in networks:
        vlan = str(n.get("vlan_id") or "-")
        dhcp = "yes" if n.get("dhcp_enabled") else "no"
        enabled = "yes" if n.get("enabled") else "no"
        print(f"  {n['name']:<30} {vlan:<6} {n.get('subnet') or '-':<20} {dhcp:<6} {enabled}")
    print()


def _print_wlans(wlans):
    if not wlans:
        print("\nNo WLANs configured.")
        return

    print(f"\n{'SSID':<25} {'Security':<12} {'WPA':<8} {'Guest':<7} {'Hidden':<7} {'Enabled'}")
    print("-" * 75)
    for w in wlans:
        guest = "yes" if w.get("is_guest") else "no"
        hidden = "yes" if w.get("hide_ssid") else "no"
        enabled = "yes" if w.get("enabled") else "no"
        print(
            f"  {w['name']:<25} {w.get('security', '-'):<12} {w.get('wpa_mode', '-'):<8} {guest:<7} {hidden:<7} {enabled}"
        )
    print()


def _print_wan(wans):
    if not wans:
        print("\nNo WAN data available.")
        return

    for w in wans:
        print(f"\nWAN Status: {w.get('status', 'unknown')}")
        print("-" * 40)
        print(f"  Uptime:     {_format_uptime(w.get('uptime_seconds'))}")
        print(f"  Latency:    {w.get('latency_avg', '-')} ms")
        if w.get("speedtest_download"):
            print(f"  Download:   {w['speedtest_download']:.1f} Mbps")
        if w.get("speedtest_upload"):
            print(f"  Upload:     {w['speedtest_upload']:.1f} Mbps")
        print(f"  RX:         {_format_bytes(w.get('rx_bytes'))}/s")
        print(f"  TX:         {_format_bytes(w.get('tx_bytes'))}/s")
    print()


def _print_ips(data):
    mode = data.get("mode", "unknown")
    enabled = data.get("enabled", False)
    categories = data.get("enabled_categories", [])

    print("\nIPS/IDS Status")
    print("-" * 40)
    print(f"  Mode:       {mode}")
    print(f"  Enabled:    {'yes' if enabled else 'no'}")
    print(f"  Categories: {len(categories)} enabled")
    if data.get("suppressed_alerts"):
        print(f"  Suppressed: {len(data['suppressed_alerts'])} alerts")
    print()


def _print_firewall(zones):
    if not zones:
        print("\nNo firewall zone data.")
        return

    print(f"\n{'Network':<30} {'VLAN':<6} {'Zone'}")
    print("-" * 50)
    for z in zones:
        vlan = str(z.get("vlan_id") or "-")
        print(f"  {z['network']:<30} {vlan:<6} {z['zone_name']}")
    print()


def _print_dns(data):
    print("\nDNS Configuration")
    print("-" * 40)
    print(f"  DoH State:  {data.get('doh_state', 'unknown')}")
    servers = data.get("dns_servers", [])
    if servers:
        print(f"  Resolvers:  {', '.join(servers)}")
    print()
