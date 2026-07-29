"""flightops CLI: flight price tracker."""

import argparse
import sys
from datetime import date, timedelta

from flightops import db, display
from flightops.amadeus_client import search_flights, search_multi_city, search_roundtrip
from flightops.fees import AIRLINE_FEES, estimate_all_in


def _require_route(route_id: int) -> dict:
    route = db.get_route(route_id)
    if not route:
        print(f"Error: route {route_id} not found.")
        sys.exit(1)
    return route


def cmd_route_add(args):
    route_id = db.add_route(
        origin=args.origin,
        destination=args.destination,
        travel_date=args.date,
        return_date=getattr(args, "return_date", None),
        passengers=args.passengers,
        target_price=args.target,
        preferred_airline=args.airline,
        nonstop_only=args.nonstop,
        label=args.label,
    )
    label = args.label or f"{args.origin.upper()}->{args.destination.upper()}"
    flags = []
    if args.nonstop:
        flags.append("nonstop only")
    if args.airline:
        flags.append(f"airline: {args.airline}")
    note = f" ({', '.join(flags)})" if flags else ""
    print(f"Added route #{route_id}: {label} ({args.date}){note}")


def cmd_route_list(args):
    routes = db.list_routes(active_only=True)
    display.print_route_list(routes)


def cmd_route_update(args):
    route = _require_route(args.id)
    updates = {}
    if args.nonstop is not None:
        updates["nonstop_only"] = int(args.nonstop)
    if args.airline is not None:
        updates["preferred_airline"] = args.airline or None
    if args.target is not None:
        updates["target_price"] = args.target
    if args.label is not None:
        updates["label"] = args.label
    if not updates:
        print("Nothing to update. Use --nonstop, --airline, --target, or --label.")
        return
    db.update_route(args.id, **updates)
    label = route.get("label") or f"{route['origin']}->{route['destination']}"
    print(f"Updated route #{args.id}: {label}: {updates}")


def cmd_route_remove(args):
    route = _require_route(args.id)
    db.remove_route(args.id)
    label = route.get("label") or f"{route['origin']}->{route['destination']}"
    print(f"Deactivated route #{args.id}: {label}")


def cmd_search(args):
    nonstop = args.nonstop
    airline = getattr(args, "airline", None)
    all_in = getattr(args, "all_in", False)
    bags = getattr(args, "bags", 1)
    return_date = getattr(args, "return_date", None)
    seat = getattr(args, "seat", "economy")

    flags = []
    if nonstop:
        flags.append("nonstop only")
    if airline:
        flags.append(f"airline: {airline}")
    if all_in:
        flags.append(f"all-in ({bags} bag/pp)")
    if return_date:
        flags.append(f"return {return_date}")
    note = f" ({', '.join(flags)})" if flags else ""
    origin = args.origin.upper()
    destination = args.destination.upper()
    print(f"Searching {origin} -> {destination} on {args.date}{note}...")

    if return_date:
        print("  Fetching round trip...", end=" ", flush=True)
        rt_results, _ = search_roundtrip(
            origin=origin,
            destination=destination,
            outbound_date=args.date,
            return_date=return_date,
            passengers=args.passengers,
            max_results=args.max,
            nonstop_only=nonstop,
            airline_filter=airline,
            seat=seat,
        )
        print(f"{len(rt_results)} results")

        print(f"  Fetching outbound one-way ({args.date})...", end=" ", flush=True)
        out_results, _ = search_flights(
            origin=origin,
            destination=destination,
            travel_date=args.date,
            passengers=args.passengers,
            max_results=args.max,
            airline_filter=airline,
            nonstop_only=nonstop,
            seat=seat,
        )
        print(f"{len(out_results)} results")

        print(f"  Fetching return one-way ({return_date})...", end=" ", flush=True)
        ret_results, _ = search_flights(
            origin=destination,
            destination=origin,
            travel_date=return_date,
            passengers=args.passengers,
            max_results=args.max,
            airline_filter=airline,
            nonstop_only=nonstop,
            seat=seat,
        )
        print(f"{len(ret_results)} results")

        def _apply_allin(results):
            if all_in:
                for r in results:
                    est = estimate_all_in(
                        r["price_per_person"], r["airline"], args.passengers, bags, include_carry_on=True
                    )
                    r["all_in_per_person"] = est["all_in_per_person"]
                    r["all_in_total"] = est["all_in_total"]
                    r["fee_breakdown"] = est["breakdown"]

        _apply_allin(rt_results)
        _apply_allin(out_results)
        _apply_allin(ret_results)

        display.print_roundtrip_comparison(
            rt_results,
            out_results,
            ret_results,
            origin,
            destination,
            args.date,
            return_date,
            args.passengers,
            all_in=all_in,
        )
        return

    try:
        results, price_level = search_flights(
            origin=args.origin,
            destination=args.destination,
            travel_date=args.date,
            passengers=args.passengers,
            max_results=args.max,
            airline_filter=airline,
            nonstop_only=nonstop,
            seat=seat,
        )
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if all_in:
        for r in results:
            est = estimate_all_in(r["price_per_person"], r["airline"], args.passengers, bags, include_carry_on=True)
            r["all_in_per_person"] = est["all_in_per_person"]
            r["all_in_total"] = est["all_in_total"]
            r["fee_breakdown"] = est["breakdown"]

    display.print_search_results(
        results,
        args.origin.upper(),
        args.destination.upper(),
        args.date,
        args.passengers,
        price_level,
        nonstop_only=nonstop,
        all_in=all_in,
    )


def cmd_poll(args):
    if args.route_id:
        routes = [_require_route(args.route_id)]
    else:
        routes = db.list_routes(active_only=True)

    if not routes:
        print("No active routes to poll. Use 'flightops route add' first.")
        return

    for route in routes:
        label = route.get("label") or f"{route['origin']}->{route['destination']}"
        airline_filter = route.get("preferred_airline")
        nonstop_only = bool(route.get("nonstop_only"))
        flags = []
        if nonstop_only:
            flags.append("nonstop")
        if airline_filter:
            flags.append(f"airline: {airline_filter}")
        note = f" ({', '.join(flags)})" if flags else ""
        print(f"Polling {label} ({route['origin']}->{route['destination']} on {route['travel_date']}){note}...")

        try:
            return_date = route.get("return_date")
            if return_date:
                results, price_level = search_roundtrip(
                    origin=route["origin"],
                    destination=route["destination"],
                    outbound_date=route["travel_date"],
                    return_date=return_date,
                    passengers=route["passengers"],
                    max_results=5,
                    airline_filter=airline_filter,
                    nonstop_only=nonstop_only,
                )
            else:
                results, price_level = search_flights(
                    origin=route["origin"],
                    destination=route["destination"],
                    travel_date=route["travel_date"],
                    passengers=route["passengers"],
                    max_results=5,
                    airline_filter=airline_filter,
                    nonstop_only=nonstop_only,
                )
        except Exception as e:
            print(f"  Error ({type(e).__name__}): {e}")
            continue

        if not results:
            print("  No results returned.")
            continue

        best = results[0]
        s = best["stops"]
        stops_str = "nonstop" if s == 0 else f"{s} stop" if isinstance(s, int) else "?"
        print(f"  Best: ${best['price_per_person']:.2f}/pp via {best['airline'] or '?'} ({stops_str}) [{price_level}]")

        db.add_snapshot(
            route_id=route["id"],
            price_total=best["price_total"],
            price_per_person=best["price_per_person"],
            currency=best["currency"],
            airline=best["airline"],
            stops=best["stops"],
            duration=best["duration"],
            departure=best.get("departure"),
            arrival=best.get("arrival"),
            price_level=price_level,
            raw=best["raw"],
        )

    print("Poll complete.")


def cmd_history(args):
    route = _require_route(args.id)
    snapshots = db.get_history(args.id)
    display.print_history(route, snapshots)


def cmd_report(args):
    route = _require_route(args.id)
    stats = db.get_stats(args.id)
    snapshots = db.get_history(args.id)
    display.print_report(route, stats, snapshots)


def cmd_fees(args):
    if args.airline:
        from flightops.fees import get_fees

        fees = get_fees(args.airline)
        display.print_airline_fees(args.airline, fees)
    else:
        display.print_all_fees(AIRLINE_FEES)


def cmd_compare(args):
    anchor = date.fromisoformat(args.around)
    dates = [anchor + timedelta(days=i) for i in range(-args.days, args.days + 1)]
    nonstop = args.nonstop
    airline = getattr(args, "airline", None)
    all_in = getattr(args, "all_in", False)
    bags = getattr(args, "bags", 1)

    flags = []
    if nonstop:
        flags.append("nonstop only")
    if airline:
        flags.append(f"airline: {airline}")
    if all_in:
        flags.append(f"all-in ({bags} bag/pp)")
    note = f" ({', '.join(flags)})" if flags else ""
    print(
        f"\nComparing {args.origin.upper()} -> {args.destination.upper()} "
        f"around {args.around} +/-{args.days} days{note}"
    )
    print(f"Searching {len(dates)} dates; this will take about {len(dates) * 5} seconds...\n")

    rows = []
    for d in dates:
        date_str = d.isoformat()
        is_anchor = d == anchor
        marker = " *" if is_anchor else ""
        print(f"  Searching {date_str}{marker}...", end=" ", flush=True)
        try:
            results, price_level = search_flights(
                origin=args.origin,
                destination=args.destination,
                travel_date=date_str,
                passengers=args.passengers,
                max_results=5,
                airline_filter=airline,
                nonstop_only=nonstop,
            )
        except RuntimeError as e:
            print(f"error: {e}")
            rows.append({"date": date_str, "is_anchor": is_anchor, "error": True})
            continue

        if results:
            best = results[0]
            row = {
                "date": date_str,
                "is_anchor": is_anchor,
                "error": False,
                "price_per_person": best["price_per_person"],
                "price_total": best["price_total"],
                "airline": best["airline"],
                "stops": best["stops"],
                "duration": best["duration"],
                "departure": best.get("departure", ""),
                "arrival": best.get("arrival", ""),
                "price_level": price_level,
            }
            if all_in:
                est = estimate_all_in(
                    best["price_per_person"], best["airline"], args.passengers, bags, include_carry_on=True
                )
                row["all_in_per_person"] = est["all_in_per_person"]
                row["all_in_total"] = est["all_in_total"]
                row["fee_breakdown"] = est["breakdown"]
                print(
                    f"${best['price_per_person']:.0f}/pp base  "
                    f"${est['all_in_per_person']:.0f}/pp all-in  via {best['airline'] or '?'}"
                )
            else:
                print(f"${best['price_per_person']:.0f}/pp via {best['airline'] or '?'}")
            rows.append(row)
        else:
            print("no results")
            rows.append({"date": date_str, "is_anchor": is_anchor, "error": True})

    print()
    display.print_compare(rows, args.origin.upper(), args.destination.upper(), args.passengers, all_in=all_in)


def cmd_trip(args):
    legs = [
        {"origin": args.origin1.upper(), "destination": args.destination1.upper(), "date": args.date1},
        {"origin": args.origin2.upper(), "destination": args.destination2.upper(), "date": args.date2},
    ]
    all_in = getattr(args, "all_in", False)
    bags = getattr(args, "bags", 1)
    nonstop = getattr(args, "nonstop", False)
    seat = getattr(args, "seat", "economy")
    airline1 = getattr(args, "airline1", None)
    airline2 = getattr(args, "airline2", None)
    airline_filters = [airline1, airline2]

    leg_label = " + ".join(f"{leg['origin']}->{leg['destination']} {leg['date']}" for leg in legs)
    flags = []
    if nonstop:
        flags.append("nonstop only")
    if all_in:
        flags.append(f"all-in ({bags} bag/pp)")
    note = f" ({', '.join(flags)})" if flags else ""
    print(f"Searching multi-city: {leg_label}{note}...")

    print("  Fetching bundled multi-city quote...", end=" ", flush=True)
    multi_results, multi_level = search_multi_city(legs, passengers=args.passengers, seat=seat)
    print(f"{len(multi_results)} results" if multi_results else "not available (see note below)")

    leg_results = []
    for leg, airline_filter in zip(legs, airline_filters):
        label = f"{leg['origin']}->{leg['destination']}"
        if airline_filter:
            label += f" ({airline_filter})"
        print(f"  Fetching {label} individually...", end=" ", flush=True)
        results, level = search_flights(
            origin=leg["origin"],
            destination=leg["destination"],
            travel_date=leg["date"],
            passengers=args.passengers,
            max_results=5,
            airline_filter=airline_filter,
            nonstop_only=nonstop,
            seat=seat,
        )
        if all_in and results:
            for r in results:
                est = estimate_all_in(r["price_per_person"], r["airline"], args.passengers, bags, include_carry_on=True)
                r["all_in_per_person"] = est["all_in_per_person"]
                r["all_in_total"] = est["all_in_total"]
                r["fee_breakdown"] = est["breakdown"]
        print(f"{len(results)} results" if results else "no results")
        leg_results.append((results, level))

    print()
    display.print_trip_results(multi_results, leg_results, legs, args.passengers, all_in=all_in)


def cmd_alerts(args):
    routes = db.list_routes(active_only=True)
    pairs = []
    for r in routes:
        if r.get("target_price"):
            snap = db.get_latest_snapshot(r["id"])
            pairs.append((r, snap))
    display.print_alerts(pairs)


def cmd_searches(args):
    from flightops import db

    counts = db.get_search_counts()
    print("\nGoogle Flights search log")
    print(f"  Total all-time : {counts['total']}")
    print(f"  This month     : {counts['this_month']}")
    if counts["by_type"]:
        by_type_str = ", ".join(f"{k} {v}" for k, v in counts["by_type"].items())
        print(f"  By type        : {by_type_str}")
    if counts["recent"]:
        print("\n  Recent (last 10):")
        for r in counts["recent"]:
            ts = r["searched_at"][:16].replace("T", " ")
            print(f"    {ts}  {r['origin']}->{r['destination']}  {r['search_type']}  {r['result_count']} results")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="flightops",
        description="Flight price tracker: monitor fares and find the best time to buy",
    )
    sub = parser.add_subparsers(dest="command")

    # --- route ---
    route_parser = sub.add_parser("route", help="Manage tracked routes")
    route_sub = route_parser.add_subparsers(dest="route_command")

    route_add = route_sub.add_parser("add", help="Add a route to track")
    route_add.add_argument("origin", help="Origin airport IATA code (e.g. ATL)")
    route_add.add_argument("destination", help="Destination airport IATA code (e.g. YVR)")
    route_add.add_argument("date", help="Travel date (YYYY-MM-DD)")
    route_add.add_argument("--return-date", help="Return date for roundtrip (YYYY-MM-DD)")
    route_add.add_argument("--passengers", type=int, default=2, help="Number of passengers (default: 2)")
    route_add.add_argument("--target", type=float, help="Alert threshold per person (e.g. 600)")
    route_add.add_argument("--airline", help="Preferred airline filter (e.g. 'Delta')")
    route_add.add_argument("--nonstop", action="store_true", help="Nonstop flights only")
    route_add.add_argument("--label", help="Friendly name (e.g. 'Anniversary outbound')")

    route_sub.add_parser("list", help="List active tracked routes")

    route_update = route_sub.add_parser("update", help="Update preferences on a tracked route")
    route_update.add_argument("id", type=int, help="Route ID")

    def _parse_bool(x: str) -> bool:
        if x.lower() in ("1", "true", "yes"):
            return True
        if x.lower() in ("0", "false", "no"):
            return False
        raise argparse.ArgumentTypeError(f"Expected true/false, got: {x!r}")

    route_update.add_argument("--nonstop", type=_parse_bool, metavar="true|false", help="Set nonstop-only preference")
    route_update.add_argument("--airline", help="Set preferred airline (empty string to clear)")
    route_update.add_argument("--target", type=float, help="Update alert threshold per person")
    route_update.add_argument("--label", help="Update friendly name")

    route_remove = route_sub.add_parser("remove", help="Deactivate a tracked route")
    route_remove.add_argument("id", type=int, help="Route ID")

    # --- search ---
    search_parser = sub.add_parser("search", help="One-off fare search (results not stored)")
    search_parser.add_argument("origin", help="Origin airport IATA code")
    search_parser.add_argument("destination", help="Destination airport IATA code")
    search_parser.add_argument("date", help="Travel date (YYYY-MM-DD)")
    search_parser.add_argument("--passengers", type=int, default=2, help="Number of passengers")
    search_parser.add_argument("--max", type=int, default=10, help="Max results to show")
    search_parser.add_argument("--airline", help="Filter to a specific airline (e.g. 'Delta')")
    search_parser.add_argument("--nonstop", action="store_true", help="Nonstop flights only")
    search_parser.add_argument(
        "--return-date", dest="return_date", help="Return date for round trip comparison (YYYY-MM-DD)"
    )
    search_parser.add_argument(
        "--all-in", action="store_true", dest="all_in", help="Show estimated all-in price including baggage fees"
    )
    search_parser.add_argument(
        "--bags", type=int, default=1, help="Checked bags per person for all-in estimate (default: 1)"
    )
    search_parser.add_argument(
        "--seat",
        default="economy",
        choices=["economy", "premium-economy", "business", "first"],
        help="Cabin class (default: economy)",
    )

    # --- poll ---
    poll_parser = sub.add_parser("poll", help="Fetch and store current prices for tracked routes")
    poll_parser.add_argument("--route-id", type=int, help="Poll a single route by ID")

    # --- history ---
    history_parser = sub.add_parser("history", help="Show price snapshot history for a route")
    history_parser.add_argument("id", type=int, help="Route ID")

    # --- report ---
    report_parser = sub.add_parser("report", help="Price trend analysis for a route")
    report_parser.add_argument("id", type=int, help="Route ID")

    # --- compare ---
    compare_parser = sub.add_parser("compare", help="Compare prices across dates around an anchor date")
    compare_parser.add_argument("origin", help="Origin airport IATA code")
    compare_parser.add_argument("destination", help="Destination airport IATA code")
    compare_parser.add_argument("--around", required=True, help="Anchor date (YYYY-MM-DD)")
    compare_parser.add_argument(
        "--days", type=int, default=2, help="Days before and after anchor to check (default: 2)"
    )
    compare_parser.add_argument("--passengers", type=int, default=2, help="Number of passengers")
    compare_parser.add_argument("--nonstop", action="store_true", help="Nonstop flights only")
    compare_parser.add_argument("--airline", help="Filter to a specific airline")
    compare_parser.add_argument(
        "--all-in", action="store_true", dest="all_in", help="Show estimated all-in price including baggage fees"
    )
    compare_parser.add_argument(
        "--bags", type=int, default=1, help="Checked bags per person for all-in estimate (default: 1)"
    )

    # --- trip ---
    trip_parser = sub.add_parser("trip", help="Search a multi-leg itinerary and compare to individual legs")
    trip_parser.add_argument("origin1", help="Leg 1 origin IATA code (e.g. ATL)")
    trip_parser.add_argument("destination1", help="Leg 1 destination IATA code (e.g. YVR)")
    trip_parser.add_argument("date1", help="Leg 1 travel date (YYYY-MM-DD)")
    trip_parser.add_argument("origin2", help="Leg 2 origin IATA code (e.g. LAX)")
    trip_parser.add_argument("destination2", help="Leg 2 destination IATA code (e.g. ATL)")
    trip_parser.add_argument("date2", help="Leg 2 travel date (YYYY-MM-DD)")
    trip_parser.add_argument("--passengers", type=int, default=2, help="Number of passengers (default: 2)")
    trip_parser.add_argument("--nonstop", action="store_true", help="Nonstop flights only on individual leg searches")
    trip_parser.add_argument("--airline1", help="Airline filter for leg 1 (e.g. 'WestJet')")
    trip_parser.add_argument("--airline2", help="Airline filter for leg 2 (e.g. 'Delta')")
    trip_parser.add_argument(
        "--all-in",
        action="store_true",
        dest="all_in",
        help="Show estimated all-in price including baggage fees on individual legs",
    )
    trip_parser.add_argument(
        "--bags", type=int, default=1, help="Checked bags per person for all-in estimate (default: 1)"
    )
    trip_parser.add_argument(
        "--seat",
        default="economy",
        choices=["economy", "premium-economy", "business", "first"],
        help="Cabin class (default: economy)",
    )

    # --- fees ---
    fees_parser = sub.add_parser("fees", help="Show airline fee structures for all-in cost estimation")
    fees_parser.add_argument("--airline", help="Show fees for a specific airline only")

    # --- alerts ---
    sub.add_parser("alerts", help="Show routes where latest price is at or below target")

    # --- searches ---
    sub.add_parser("searches", help="Show search log counts (total, this month, by type)")

    args = parser.parse_args()

    dispatch = {
        ("route", "add"): cmd_route_add,
        ("route", "list"): cmd_route_list,
        ("route", "update"): cmd_route_update,
        ("route", "remove"): cmd_route_remove,
        ("search", None): cmd_search,
        ("poll", None): cmd_poll,
        ("history", None): cmd_history,
        ("report", None): cmd_report,
        ("compare", None): cmd_compare,
        ("trip", None): cmd_trip,
        ("fees", None): cmd_fees,
        ("alerts", None): cmd_alerts,
        ("searches", None): cmd_searches,
    }

    key = (args.command, getattr(args, "route_command", None))
    handler = dispatch.get(key)

    if handler:
        handler(args)
    else:
        parser.print_help()
