"""Terminal output formatting for flightops."""

from datetime import date, datetime


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _days_until(travel_date: str) -> int:
    d = date.fromisoformat(travel_date)
    return (d - date.today()).days


def _stops_str(stops) -> str:
    if stops == 0:
        return "nonstop"
    if isinstance(stops, int):
        return f"{stops} stop"
    return "?"


def _short_time(ts: str) -> str:
    """'8:15 AM\xa0on\xa0Thu, Sep 24' -> '8:15 AM Sep 24'"""
    if not ts:
        return ""
    import re

    # Normalize unicode whitespace (narrow no-break space, non-breaking space) to regular space
    ts = re.sub(r"[ \xa0]", " ", ts)
    # Strip " on " connector and day-of-week prefix that Google includes in departure/arrival
    ts = re.sub(r"\s+on\s+", " ", ts)
    ts = re.sub(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*", "", ts)
    return ts[:15]


def _price_level_label(level: str) -> str:
    labels = {"low": "LOW (good time to buy)", "typical": "typical", "high": "HIGH (prices elevated)"}
    return labels.get(level, level)


def print_route_list(routes: list):
    if not routes:
        print("\nNo active routes. Use 'flightops route add' to add one.")
        return

    print(
        f"\n{'ID':<4} {'Label':<28} {'Route':<10} {'Date':<12} {'PAX':<5} {'Nonstop':<9} {'Airline':<12} {'Target':<10} {'Days Out'}"
    )
    print("-" * 105)
    for r in routes:
        route = f"{r['origin']}->{r['destination']}"
        target = f"${r['target_price']:.0f}/pp" if r["target_price"] else "-"
        days = _days_until(r["travel_date"])
        label = (r["label"] or "")[:27]
        airline = (r.get("preferred_airline") or "any")[:11]
        nonstop = "yes" if r.get("nonstop_only") else "no"
        print(
            f"{r['id']:<4} {label:<28} {route:<10} {r['travel_date']:<12} {r['passengers']:<5} {nonstop:<9} {airline:<12} {target:<10} {days}d"
        )
    print()


def _fee_str(val: int, label: str = "person") -> str:
    return "included" if val == 0 else f"${val}/{label}"


def print_airline_fees(name: str, fees: dict):
    print(f"\nFee structure: {name}")
    print("-" * 55)
    print(f"  Carry-on bag     : {_fee_str(fees['carry_on'])}")
    print(f"  1st checked bag  : {_fee_str(fees['checked_1'])}")
    print(f"  2nd checked bag  : {_fee_str(fees['checked_2'])}")
    seat_str = "free" if fees["seat_basic"] == 0 else f"~${fees['seat_basic']}/person"
    print(f"  Seat selection   : {seat_str}")
    if fees.get("notes"):
        print(f"\n  Note: {fees['notes']}")
    print()


def print_all_fees(airline_fees: dict):
    print(f"\n{'Airline':<14} {'Carry-on':<12} {'Bag 1':<10} {'Bag 2':<10} {'Seat':<8} Notes")
    print("-" * 100)
    for name, fees in sorted(airline_fees.items()):
        carry = "included" if fees["carry_on"] == 0 else f"${fees['carry_on']}"
        bag1 = "included" if fees["checked_1"] == 0 else f"${fees['checked_1']}"
        bag2 = "included" if fees["checked_2"] == 0 else f"${fees['checked_2']}"
        seat = "free" if fees["seat_basic"] == 0 else f"~${fees['seat_basic']}"
        notes = fees.get("notes", "")[:52]
        print(f"  {name:<12} {carry:<12} {bag1:<10} {bag2:<10} {seat:<8} {notes}")
    print()


def _print_fee_breakdown(rows: list):
    """Print a per-airline fee breakdown section for all-in results."""
    seen = {}
    for r in rows:
        if r.get("error"):
            continue
        airline = r.get("airline") or "?"
        if airline not in seen:
            seen[airline] = r
    if not seen:
        return

    print("  Fee breakdown:")
    for airline, r in seen.items():
        base = r["price_per_person"]
        breakdown = r.get("fee_breakdown") or []
        all_in = r.get("all_in_per_person", base)
        if breakdown:
            parts = "  +  ".join(breakdown)
            print(f"    {airline:<18}  base ${base:.2f}  +  {parts}  =  ${all_in:.2f}/pp")
        else:
            print(f"    {airline:<18}  base ${base:.2f}  (all fees included)  =  ${all_in:.2f}/pp")
    print()


def print_search_results(
    results: list,
    origin: str,
    destination: str,
    travel_date: str,
    passengers: int,
    price_level: str = "",
    nonstop_only: bool = False,
    all_in: bool = False,
):
    if not results:
        print(f"\nNo flights found for {origin}->{destination} on {travel_date}.")
        return

    level_str = ""
    nonstop_str = "  |  nonstop only" if nonstop_only else ""
    print(
        f"\nFlights: {origin} -> {destination}  |  {travel_date}  |  {passengers} passenger(s){nonstop_str}{level_str}"
    )
    if all_in:
        print("-" * 96)
        print(
            f"  {'Airline':<20} {'Departs':<16} {'Arrives':<16} {'Stops':<9} {'Duration':<13} {'Base/pp':<10} {'All-in/pp':<12} {'All-in Total'}"
        )
        print("-" * 96)
        for r in results:
            dep_short = _short_time(r.get("departure", ""))
            arr_short = _short_time(r.get("arrival", ""))
            duration = r.get("duration", "")[:12]
            print(
                f"  {r['airline'] or '?':<20} {dep_short:<16} {arr_short:<16}"
                f" {_stops_str(r['stops']):<9} {duration:<13}"
                f" ${r['price_per_person']:<9.2f} ${r.get('all_in_per_person', r['price_per_person']):<11.2f}"
                f" ${r.get('all_in_total', r['price_total']):.2f}"
            )
        print()
        _print_fee_breakdown(results)
    else:
        print("-" * 96)
        print(
            f"  {'Airline':<20} {'Departs':<16} {'Arrives':<16} {'Stops':<9} {'Duration':<14} {'Per Person':<13} {'Total'}"
        )
        print("-" * 96)
        for r in results:
            dep_short = _short_time(r.get("departure", ""))
            arr_short = _short_time(r.get("arrival", ""))
            duration = r.get("duration", "")[:13]
            print(
                f"  {r['airline'] or '?':<20} {dep_short:<16} {arr_short:<16}"
                f" {_stops_str(r['stops']):<9} {duration:<14}"
                f" ${r['price_per_person']:<12.2f} ${r['price_total']:.2f}"
            )
        print()


def print_history(route: dict, snapshots: list):
    label = route.get("label") or f"{route['origin']}->{route['destination']}"
    print(f"\nPrice history: {label}")
    print(
        f"Route: {route['origin']} -> {route['destination']}  |  {route['travel_date']}  |  {route['passengers']} pax"
    )
    if route.get("preferred_airline"):
        print(f"Airline filter: {route['preferred_airline']}")
    print("-" * 80)

    if not snapshots:
        print("  No price snapshots yet. Run 'flightops poll' to collect data.")
        print()
        return

    print(f"  {'Date/Time':<22} {'Airline':<18} {'Departs':<16} {'Stops':<9} {'Per Person':<13} {'Total'}")
    print("-" * 80)
    for s in snapshots:
        ts = _parse_iso(s["fetched_at"]).strftime("%Y-%m-%d %H:%M")
        departs = (s.get("departure") or "")[:15]
        print(
            f"  {ts:<22} {s['airline'] or '?':<18} {departs:<16}"
            f" {_stops_str(s['stops']):<9} ${s['price_per_person']:<12.2f} ${s['price_total']:.2f}"
        )
    print()


def print_report(route: dict, stats: dict, snapshots: list):
    label = route.get("label") or f"{route['origin']}->{route['destination']}"
    days_out = _days_until(route["travel_date"])
    print(f"\nReport: {label}")
    print(f"Route: {route['origin']} -> {route['destination']}  |  {route['travel_date']}  |  {days_out} days out")
    if route.get("preferred_airline"):
        print(f"Airline filter: {route['preferred_airline']}")
    print("-" * 60)

    if not stats or stats["snapshot_count"] == 0:
        print("  No price data yet. Run 'flightops poll' to collect data.")
        print()
        return

    print(f"  Snapshots collected : {stats['snapshot_count']}")
    print(f"  First seen          : {stats['first_seen'][:10]}")
    print(f"  Last seen           : {stats['last_seen'][:10]}")
    print(f"  Min (per person)    : ${stats['min_price']:.2f}")
    print(f"  Max (per person)    : ${stats['max_price']:.2f}")
    print(f"  Avg (per person)    : ${stats['avg_price']:.2f}")

    target = route.get("target_price")
    if target:
        print(f"  Target price        : ${target:.2f}/pp")
        if stats["min_price"] <= target:
            print("  [TARGET HIT] Best observed price is at or below target")

    if len(snapshots) >= 4:
        mid = len(snapshots) // 2
        first_avg = sum(s["price_per_person"] for s in snapshots[:mid]) / mid
        second_avg = sum(s["price_per_person"] for s in snapshots[mid:]) / (len(snapshots) - mid)
        delta = second_avg - first_avg
        direction = "up" if delta > 0 else "down"
        print(f"  Trend               : {direction} ${abs(delta):.2f}/pp vs. earlier average")

    print()


def print_compare(rows: list, origin: str, destination: str, passengers: int, all_in: bool = False):
    valid = [r for r in rows if not r.get("error")]
    if not valid:
        print("No results to compare.")
        return

    anchor_row = next((r for r in valid if r["is_anchor"]), None)
    price_key = "all_in_per_person" if all_in else "price_per_person"
    anchor_price = anchor_row.get(price_key, anchor_row["price_per_person"]) if anchor_row else None

    price_col = "All-in/pp" if all_in else "Per Person"
    print(
        f"{'Date':<13} {'Airline':<20} {'Departs':<16} {'Arrives':<16} {'Stops':<9} {'Duration':<13} {price_col:<13} {'Total':<12} {'vs Anchor'}"
    )
    print("-" * 128)

    for r in rows:
        marker = "  <--" if r["is_anchor"] else ""
        if r.get("error"):
            print(f"{r['date']:<13} {'(no results)'}")
            continue

        dep = _short_time(r.get("departure", ""))
        arr = _short_time(r.get("arrival", ""))
        airline = (r.get("airline") or "?")[:19]
        duration = (r.get("duration") or "")[:12]
        stops = _stops_str(r.get("stops"))
        ppp = r.get(price_key, r["price_per_person"])
        total = r.get("all_in_total" if all_in else "price_total", r["price_total"])
        anchor_marker = "*" if r["is_anchor"] else " "

        if anchor_price is not None and not r["is_anchor"]:
            delta = ppp - anchor_price
            delta_str = f"+${delta:.0f}" if delta > 0 else f"-${abs(delta):.0f}"
        elif r["is_anchor"]:
            delta_str = "anchor"
        else:
            delta_str = ""

        print(
            f"{anchor_marker}{r['date']:<12} {airline:<20} {dep:<16} {arr:<16}"
            f" {stops:<9} {duration:<13} ${ppp:<12.2f} ${total:<11.2f} {delta_str}{marker}"
        )

    print()

    if len(valid) > 1:
        best_overall = min(valid, key=lambda r: r.get(price_key, r["price_per_person"]))
        worst_overall = max(valid, key=lambda r: r.get(price_key, r["price_per_person"]))
        best_ppp = best_overall.get(price_key, best_overall["price_per_person"])
        worst_ppp = worst_overall.get(price_key, worst_overall["price_per_person"])
        spread = worst_ppp - best_ppp
        label = "all-in" if all_in else "base"

        print(
            f"  Cheapest date : {best_overall['date']}  ${best_ppp:.2f}/pp {label}  via {best_overall['airline'] or '?'}"
        )
        print(
            f"  Priciest date : {worst_overall['date']}  ${worst_ppp:.2f}/pp {label}  via {worst_overall['airline'] or '?'}"
        )
        print(f"  Price spread  : ${spread:.0f}/pp  (${spread * passengers:.0f} total for {passengers} passengers)")

        if anchor_price is not None:
            cheaper = [
                r for r in valid if not r["is_anchor"] and r.get(price_key, r["price_per_person"]) < anchor_price
            ]
            if cheaper:
                best_alt = min(cheaper, key=lambda r: r.get(price_key, r["price_per_person"]))
                saved_pp = anchor_price - best_alt.get(price_key, best_alt["price_per_person"])
                saved_total = saved_pp * passengers
                print(f"  vs anchor     : {best_alt['date']} saves ${saved_pp:.0f}/pp (${saved_total:.0f} total)")
            else:
                print("  vs anchor     : anchor date matches or beats all other dates in this window")
        else:
            print("  (anchor date had no results this run; re-run to get delta comparison)")
        print()

    if all_in:
        _print_fee_breakdown(valid)


def print_roundtrip_comparison(
    rt_results: list,
    out_results: list,
    ret_results: list,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str,
    passengers: int,
    all_in: bool = False,
):
    """Print round trip bundled price vs two one-ways side by side."""
    print(
        f"\nRound trip: {origin} -> {destination}  |  {outbound_date} out / {return_date} return  |  {passengers} passenger(s)"
    )
    print("=" * 72)

    price_col = "All-in/pp" if all_in else "Per Person"
    rt_key = "all_in_per_person" if all_in else "price_per_person"
    rt_total_key = "all_in_total" if all_in else "price_total"

    def _section(label, results, max_rows=5):
        print(f"\n  {label}:")
        print(f"  {'Airline':<22} {'Departs':<18} {'Stops':<10} {price_col:<13} {'Total'}")
        print("  " + "-" * 72)
        if not results:
            print("  No results returned.")
            return
        for r in results[:max_rows]:
            airline = (r.get("airline") or "?")[:21]
            dep = _short_time(r.get("departure", ""))[:17]
            stops = _stops_str(r.get("stops"))
            ppp = r.get(rt_key, r["price_per_person"])
            total = r.get(rt_total_key, r["price_total"])
            print(f"  {airline:<22} {dep:<18} {stops:<10} ${ppp:<12.2f} ${total:.2f}")

    _section(f"Round trip bundled (outbound {outbound_date})", rt_results)
    _section(f"Outbound one-way ({outbound_date})", out_results)
    _section(f"Return one-way ({return_date})", ret_results)

    # Best comparison summary: pick cheapest by the active price key, not insertion order
    if rt_results and out_results and ret_results:
        best_rt = min(rt_results, key=lambda r: r.get(rt_key, r["price_per_person"]))
        best_out = min(out_results, key=lambda r: r.get(rt_key, r["price_per_person"]))
        best_ret = min(ret_results, key=lambda r: r.get(rt_key, r["price_per_person"]))

        rt_pp = best_rt.get(rt_key, best_rt["price_per_person"])
        rt_total = best_rt.get(rt_total_key, best_rt["price_total"])
        out_pp = best_out.get(rt_key, best_out["price_per_person"])
        ret_pp = best_ret.get(rt_key, best_ret["price_per_person"])
        combined_pp = out_pp + ret_pp
        combined_total = combined_pp * passengers

        print(f"\n  Best comparison ({price_col.lower()}):")
        print("  " + "-" * 60)
        print(f"  Round trip      {best_rt['airline'] or '?':<18} ${rt_pp:.2f}/pp    ${rt_total:.2f} total")
        print(
            f"  Two one-ways    {(best_out['airline'] or '?')[:8]}/{(best_ret['airline'] or '?')[:8]:<10} ${combined_pp:.2f}/pp    ${combined_total:.2f} total"
        )
        savings = combined_total - rt_total
        if savings > 0:
            pct = int(savings / combined_total * 100)
            print(f"  Round trip saves ${savings:.0f} total ({pct}%)")
        else:
            print(f"  Two one-ways save ${abs(savings):.0f} total vs round trip")
    print()


def print_trip_results(
    multi_results: list,
    leg_results: list[tuple[list[dict], str]],
    legs_meta: list[dict],
    passengers: int,
    all_in: bool = False,
):
    """Print multi-city bundled price vs individual legs side by side.

    leg_results: list of (results_list, price_level) tuples, one per leg.
    legs_meta: list of {"origin", "destination", "date"} dicts, one per leg.
    """
    leg_label = " + ".join(f"{m['origin']}->{m['destination']} {m['date'][5:]}" for m in legs_meta)
    print(f"\nMulti-city trip: {leg_label}  |  {passengers} passenger(s)")
    print("=" * 72)

    print("\n  Bundled multi-city (Google Flights):")
    if multi_results:
        print("  Note: bundled price may or may not include bag fees; verify at booking.")
        print(f"  {'Airline':<22} {'Per Person':<13} {'Total'}")
        print("  " + "-" * 48)
        for r in multi_results[:3]:
            airline = (r.get("airline") or "?")[:21]
            print(f"  {airline:<22} ${r['price_per_person']:<12.2f} ${r['price_total']:.2f}")
    else:
        print("  No bundled results returned. Try individual leg searches above.")

    print("\n  Individual legs (searched separately):")
    print(f"  {'Leg':<18} {'Airline':<20} {'Base/pp':<11}", end="")
    if all_in:
        print(f" {'All-in/pp':<12} {'Total (2 pax)'}", end="")
    else:
        print(f" {'Total (2 pax)'}", end="")
    print()
    print("  " + "-" * (60 if not all_in else 74))

    combined_base_pp = 0.0
    combined_allin_pp = 0.0
    combined_base_total = 0.0
    combined_allin_total = 0.0

    for meta, (results, _) in zip(legs_meta, leg_results):
        leg_str = f"{meta['origin']}->{meta['destination']} {meta['date'][5:]}"
        if not results:
            print(f"  {leg_str:<18} {'(no results)'}")
            continue
        best = results[0]
        airline = (best.get("airline") or "?")[:19]
        base_pp = best["price_per_person"]
        base_total = best["price_total"]
        combined_base_pp += base_pp
        combined_base_total += base_total

        if all_in:
            allin_pp = best.get("all_in_per_person", base_pp)
            allin_total = best.get("all_in_total", base_total)
            combined_allin_pp += allin_pp
            combined_allin_total += allin_total
            print(f"  {leg_str:<18} {airline:<20} ${base_pp:<10.2f} ${allin_pp:<11.2f} ${allin_total:.2f}")
        else:
            print(f"  {leg_str:<18} {airline:<20} ${base_pp:<10.2f} ${base_total:.2f}")

    print("  " + "-" * (60 if not all_in else 74))
    if all_in:
        print(f"  {'Combined base':<18} {'':<20} ${combined_base_pp:<10.2f} {'':<12} ${combined_base_total:.2f}")
        print(f"  {'Combined all-in':<18} {'':<20} {'':<11} ${combined_allin_pp:<11.2f} ${combined_allin_total:.2f}")
    else:
        print(f"  {'Combined base':<18} {'':<20} ${combined_base_pp:<10.2f} ${combined_base_total:.2f}")

    if multi_results and combined_base_total > 0:
        bundled_total = multi_results[0]["price_total"]
        compare_total = combined_allin_total if all_in else combined_base_total
        savings = compare_total - bundled_total
        if savings > 0:
            label = "all-in individual" if all_in else "base individual"
            print(f"\n  Bundled saves ~${savings:.0f} total vs {label} legs")
        else:
            print(f"\n  Bundled is ~${abs(savings):.0f} more than {('all-in' if all_in else 'base')} individual legs")
    print()


def print_alerts(routes_with_latest: list):
    triggered = [
        (r, snap)
        for r, snap in routes_with_latest
        if r.get("target_price") and snap and snap["price_per_person"] <= r["target_price"]
    ]

    if not triggered:
        print("\nNo price alerts triggered. All tracked routes are above target.")
        print()
        return

    print(f"\nPRICE ALERTS ({len(triggered)} route(s) at or below target)")
    print("-" * 60)
    for r, snap in triggered:
        label = r.get("label") or f"{r['origin']}->{r['destination']}"
        print(
            f"  {label}: ${snap['price_per_person']:.2f}/pp"
            f" (target ${r['target_price']:.2f}/pp)"
            f" via {snap['airline'] or '?'}"
        )
    print()
