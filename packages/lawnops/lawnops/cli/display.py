"""Display formatting for all CLI output. Receives data, prints it."""

from datetime import datetime

from lawnops.advisory import consecutive_days_above, trend_direction

# Constants for display characters (avoid backslash-in-fstring issues)
_DASH = "\u2014"


def spark_bar(value, min_val, max_val, width=15):
    """Create ASCII spark bar."""
    if max_val == min_val:
        filled = width // 2
    else:
        ratio = (value - min_val) / (max_val - min_val)
        filled = int(ratio * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def display_now(current, daily_data, config, pollen_data=None):
    threshold = config["thresholds"]["pre_emergent_soil_temp"]
    consec = consecutive_days_above(daily_data, threshold)
    direction, diff = trend_direction(daily_data)

    print(f"\n\U0001f321  Soil Temperature Monitor \u2014 {config['location']['name']}")
    print("\u2500" * 50)
    print(f"  Time:        {current['time'].replace('T', ' ')}")
    print(f"  Soil (6cm):  {current['soil_temp']:.1f}\u00b0F", end="")
    if current["soil_temp"] >= threshold:
        print(f"  \u26a0\ufe0f  ABOVE {threshold}\u00b0F threshold")
    else:
        print(f"  ({threshold - current['soil_temp']:.1f}\u00b0F below threshold)")
    print(f"  Air:         {current['air_temp']:.1f}\u00b0F")
    print(f"  Wind:        {current['wind']:.1f} mph")
    print(f'  Precip:      {current["precip"]:.2f}"')
    if pollen_data:
        print(f"  Pollen:      {pollen_data['total_count']} ({pollen_data['category']}) \U0001f33c")
    print(f"  Trend:       {direction} ({diff:+.1f}\u00b0F vs prior 3 days)")
    print(f"  Days \u2265{threshold}\u00b0F:  {consec} consecutive")
    print()


def display_trend(daily_data, config):
    threshold = config["thresholds"]["pre_emergent_soil_temp"]
    today = datetime.now().strftime("%Y-%m-%d")

    historical = [d for d in daily_data if d["date"] <= today]
    forecast = [d for d in daily_data if d["date"] > today]

    all_soil = [d["soil_avg"] for d in daily_data]
    if not all_soil:
        print("No data available.")
        return
    global_min = min(d["soil_min"] for d in daily_data)
    global_max = max(d["soil_max"] for d in daily_data)

    print(f"\n\U0001f4ca 14-Day Soil Temperature Trend \u2014 {config['location']['name']}")
    print("\u2500" * 78)
    print(f"  {'Date':<12} {'Min':>5} {'Avg':>5} {'Max':>5}  {'Rain':>5}  {'Trend':<15}  {'Thr'}")
    print("\u2500" * 78)

    for d in historical:
        bar = spark_bar(d["soil_avg"], global_min, global_max)
        rain = f'{d["precip_total"]:.2f}"' if d["precip_total"] > 0 else "  -  "
        thr = "\u26a0" if d["soil_max"] >= threshold else " "
        print(
            f"  {d['date']:<12} {d['soil_min']:5.1f} {d['soil_avg']:5.1f} {d['soil_max']:5.1f}  {rain:>5}  {bar}  {thr}"
        )

    if forecast:
        print("  " + "\u00b7 " * 35 + " (forecast)")
        for d in forecast:
            bar = spark_bar(d["soil_avg"], global_min, global_max)
            rain = f'{d["precip_total"]:.2f}"' if d["precip_total"] > 0 else "  -  "
            thr = "\u26a0" if d["soil_max"] >= threshold else " "
            print(
                f"  {d['date']:<12} {d['soil_min']:5.1f} {d['soil_avg']:5.1f} {d['soil_max']:5.1f}  {rain:>5}  {bar}  {thr}"
            )

    print(f"\n  Threshold: {threshold}\u00b0F (\u26a0 = daily max at or above)")
    print()


def display_advisory(current, daily_data, config):
    from lawnops.advisory import pre_emergent_advisory

    display_now(current, daily_data, config)
    display_trend(daily_data, config)

    level, message = pre_emergent_advisory(daily_data, config)
    icons = {
        "SAFE": "\u2705",
        "WARNING": "\u26a0\ufe0f ",
        "URGENT": "\U0001f534",
        "LATE": "\U0001f6a8",
        "UNKNOWN": "\u2753",
    }
    icon = icons.get(level, "")

    print(f"\U0001f331 Pre-Emergent Advisory: {icon} {level}")
    print("\u2500" * 50)
    print(f"  {message}")
    print()


def display_spray(result, config):
    status_icon = "\u2705" if result["status"] == "GO" else "\u274c"
    print(f"\n\U0001f9ea Spray Window Assessment \u2014 {config['location']['name']}")
    print("\u2500" * 50)
    print(f"  Status: {status_icon} {result['status']}")
    print()

    if result.get("soil_temp") is not None:
        soil_icon = "\u2705" if result["soil_ok"] else "\u274c"
        print(
            f"  {soil_icon} Soil temp:  {result['soil_temp']:.1f}\u00b0F (need \u2265{config['thresholds']['spray_soil_temp']}\u00b0F)"
        )

    air_min, air_max = result["air_range"]
    air_ok = air_max >= config["thresholds"]["spray_air_temp"]
    air_icon = "\u2705" if air_ok else "\u274c"
    print(
        f"  {air_icon} Air temp:   {air_min:.0f}-{air_max:.0f}\u00b0F (need \u2265{config['thresholds']['spray_air_temp']}\u00b0F)"
    )

    rain_ok = result["total_precip"] == 0
    rain_icon = "\u2705" if rain_ok else "\u274c"
    print(f'  {rain_icon} Rain (48h): {result["total_precip"]:.2f}" (need 0")')

    wind_ok = result["max_wind"] < config["thresholds"]["spray_max_wind_mph"]
    wind_icon = "\u2705" if wind_ok else "\u274c"
    print(
        f"  {wind_icon} Wind:       max {result['max_wind']:.0f} mph (need <{config['thresholds']['spray_max_wind_mph']:.0f} mph)"
    )

    # Pollen impact (advisory, not a gate)
    if result.get("pollen_impact"):
        impact = result["pollen_impact"]
        count = result.get("pollen_count", "?")
        impact_icons = {"none": "\u2705", "low": "\u2705", "moderate": "\u26a0\ufe0f", "high": "\u274c"}
        icon = impact_icons.get(impact["impact"], "\u2753")
        print(f"  {icon} Pollen:     {count} {_DASH} {impact['impact']} impact")

    if result["issues"]:
        print("\n  Issues:")
        for issue in result["issues"]:
            print(f"    \u2022 {issue}")

    if result.get("mow_conflict"):
        next_mow = result.get("next_mow_date", "unknown")
        print(f"\n  \u26a0\ufe0f  Mow buffer conflict {_DASH} next mow: {next_mow}")
        print("     Avoid spraying during the no-mow buffer window.")

    if result["windows"]:
        print("\n  Best spray windows:")
        for w in result["windows"][:3]:
            start = w[0]["time"].replace("T", " ")
            end = w[-1]["time"].replace("T", " ")
            print(f"    \u2022 {start} \u2192 {end} ({len(w)} hours)")
    elif result["status"] == "NO-GO":
        print("\n  No suitable spray windows in next 48 hours.")

    print()


def display_pollen(pollen_data, config):
    """Display current pollen count with breakdown and spray impact."""
    from lawnops.pollen import pollen_spray_impact

    print(f"\n\U0001f33c Pollen Count {_DASH} Atlanta (NAB Station)")
    print("\u2500" * 50)
    print(f"  Date:      {pollen_data['date']}")
    print(f"  Count:     {pollen_data['total_count']}")
    print(f"  Category:  {pollen_data['category'].upper()}")

    if pollen_data.get("trees"):
        print(f"  Trees:     {', '.join(pollen_data['trees'])}")
    if pollen_data.get("grasses"):
        print(f"  Grasses:   {', '.join(pollen_data['grasses'])}")
    if pollen_data.get("weeds"):
        print(f"  Weeds:     {', '.join(pollen_data['weeds'])}")
    if pollen_data.get("molds"):
        print(f"  Molds:     {', '.join(pollen_data['molds'])}")

    impact = pollen_spray_impact(pollen_data["total_count"], config)
    impact_icons = {
        "none": "\u2705",
        "low": "\u2705",
        "moderate": "\u26a0\ufe0f",
        "high": "\u274c",
        "unknown": "\u2753",
    }
    icon = impact_icons.get(impact["impact"], "")
    print(f"  Spray:     {icon} {impact['note']}")
    print()


def display_pollen_trend(history, days):
    """Display pollen count trend from database history."""
    from lawnops.pollen import get_pollen_trend_analysis

    print(f"\n\U0001f33c Pollen Trend {_DASH} Last {days} Days")
    print("\u2500" * 50)

    if not history:
        print("  No pollen data recorded yet. Run 'lawnops pollen' to start logging.")
        print()
        return

    print(f"  {'Date':<12} {'Count':>6}  {'Category':<16}")
    print("  " + "\u2500" * 40)
    for entry in history:
        print(f"  {entry['date']:<12} {entry['total_count']:>6}  {entry['category']:<16}")

    trend = get_pollen_trend_analysis(history)
    if trend:
        arrows = {"rising": "\u2191", "falling": "\u2193", "stable": "\u2194"}
        arrow = arrows.get(trend["direction"], "")
        print("  " + "\u2500" * 40)
        print(f"  Avg:   {trend['avg']:.0f}    Trend: {trend['direction'].title()} {arrow}")
        print(f"  Peak:  {trend['peak_count']}    ({trend['peak_date']})")
    print()


def display_coverage(result):
    """Display coverage calculation results."""
    print(f"\n\U0001f4d0 Coverage Calculator {_DASH} {result['product'].title()}")
    print("\u2500" * 50)
    print(f"  Product:    {result['product'].title()}")
    print(f"  Type:       {result['type']}")
    print(f"  Yard area:  {result['yard_sqft']:,} sq ft")
    if result.get("bag_size_lbs"):
        print(f"  Bag size:   {result['bag_size_lbs']} lbs")
    print(f"  Coverage:   {result['unit_coverage_sqft']:,} sq ft per {result['unit']}")
    print(f"  Needed:     {result['units_needed']} {result['unit']}(s)")
    print(f"  Surplus:    {result['surplus_sqft']:,} sq ft extra coverage")
    if result.get("notes"):
        print(f"  Notes:      {result['notes']}")
    print()


def display_mix(result):
    """Display mix rate calculation results."""
    print(f"\n\U0001f9ea Mix Rate Calculator {_DASH} {result['product'].title()}")
    print("\u2500" * 50)
    print(f"  Product:       {result['product'].title()}")
    print(f"  Rate type:     {result['rate_type']}")
    print(f"  Tank size:     {result['tank_gallons']:.1f} gal")
    print(f"  Concentrate:   {result['concentrate_oz']:.1f} oz per tank")
    print(f"  Rate:          {result['oz_per_gallon']:.1f} oz/gal")
    print(f"  Coverage/tank: {result['coverage_per_tank']:,} sq ft")
    print(f"  Yard area:     {result['yard_sqft']:,} sq ft")
    print(f"  Tanks needed:  {result['tanks_for_yard']}")
    if result.get("notes"):
        print(f"  Notes:         {result['notes']}")
    print()


def display_window(result, product_type, config):
    """Display application window results with day-by-day scoring table."""
    status_icons = {"GO": "\u2705", "CAUTION": "\u26a0\ufe0f ", "NO-GO": "\u274c"}
    icon = status_icons.get(result["status"], "")
    print(f"\n\U0001f4c5 Application Window {_DASH} {product_type.title()}")
    print("\u2500" * 78)
    print(f"  Status: {icon} {result['status']}")

    if not result["all_days"]:
        print("  No forecast data available.")
        print()
        return

    best_date = result["best_day"]["date"] if result["best_day"] else None

    print(f"\n  {'Date':<12} {'Score':>5}  {'Air':>5}  {'Wind':>5}  {'Rain':>6}  {'Issues'}")
    print("  " + "\u2500" * 74)
    for d in result["all_days"]:
        marker = " \u2b50" if d["date"] == best_date else ""
        air_str = f"{d['air_max']:.0f}F" if d["air_max"] is not None else "  - "
        wind_str = f"{d['wind_max']:.0f}" if d["wind_max"] is not None else " - "
        rain_str = f'{d["precip"]:.2f}"' if d["precip"] else "  -  "
        issues_str = "; ".join(d["issues"]) if d["issues"] else "Clear"
        print(f"  {d['date']:<12} {d['score']:>5}  {air_str:>5}  {wind_str:>5}  {rain_str:>6}  {issues_str}{marker}")

    if best_date:
        print(f"\n  \u2b50 Recommended: {best_date} (score {result['best_day']['score']})")
    print()


def display_irrigation_status(ctrl, sensors, programs):
    online_icon = "\u2705" if ctrl.online else "\u274c"
    last_contact = ctrl.last_contact_time.strftime("%Y-%m-%d %H:%M") if ctrl.last_contact_time else "Unknown"
    status_msg = ctrl.status.summary if ctrl.status else "Unknown"

    print(f"\n\U0001f4a7 Irrigation Status \u2014 {ctrl.name}")
    print("\u2500" * 60)
    print(f"  Controller:   {ctrl.name} (fw {ctrl.software_version})")
    print(f"  Online:       {online_icon} {'Yes' if ctrl.online else 'No'}")
    print(f"  Status:       {status_msg}")
    print(f"  Last Contact: {last_contact}")

    for sensor in sensors:
        print(
            f"  {sensor.name}:  {sensor.status.water_flow if hasattr(sensor.status, 'water_flow') else sensor.status}"
        )

    if programs:
        print("\n  Programs:")
        print("  " + "\u2500" * 56)
        for prog in programs.values():
            zones_str = ", ".join(str(z) for z in sorted(prog["zones"]))
            monthly = prog["monthly"]
            current_month = datetime.now().month - 1
            current_pct = monthly[current_month] if current_month < len(monthly) else "?"
            print(
                f"  {prog['name']:<16} Start: {prog['start']}  Every {prog['period']}d  ET: {current_pct}% this month"
            )
            print(f"  {'':16} Zones: {zones_str}")
            print(f"  {'':16} Monthly %: {' '.join(str(m).rjust(3) for m in monthly)}")
            print(f"  {'':16}            {'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'}")

    print(f"\n  {'Zone':<8} {'Name':<28} {'Status':<12} {'Adj%':>5}  {'Next Run'}")
    print("  " + "\u2500" * 64)
    for zone in ctrl.zones:
        zone_num = zone.number.value
        name = zone.name
        adj_pct = zone.watering_settings.fixed_watering_adjustment

        sched = zone.scheduled_runs
        if sched.current_run:
            remaining = sched.current_run.remaining_time
            mins = remaining.total_seconds() / 60 if remaining else 0
            zone_status = f"Running ({mins:.0f}m)"
        elif zone.suspensions:
            zone_status = "Suspended"
        else:
            zone_status = "Ready"

        if sched.next_run:
            next_run = (
                sched.next_run.start_time.strftime("%Y-%m-%d %H:%M")
                if hasattr(sched.next_run, "start_time")
                else str(sched.next_run)
            )
        elif sched.summary:
            next_run = sched.summary
        else:
            next_run = "\u2014"

        print(f"  {zone_num:<8} {name:<28} {zone_status:<12} {adj_pct:>4}%  {next_run}")

    suspended = [z for z in ctrl.zones if z.suspensions]
    if suspended:
        print("\n  Active Suspensions:")
        for zone in suspended:
            s = zone.suspensions[0]
            print(f"    Zone {zone.number.value} ({zone.name}): until {s.end_time.strftime('%Y-%m-%d %H:%M')}")

    print()


def display_irrigation_run(zone_name, zone_num, minutes):
    print(f"\n\U0001f4a7 Started {zone_name} (Zone {zone_num}) for {minutes} minutes")
    print()


def display_irrigation_runall(queued_zones, minutes):
    print(f"\n\U0001f4a7 Queued {len(queued_zones)} zones for {minutes} min each (sequential):")
    for zone_num, zone_name in queued_zones:
        print(f"    Zone {zone_num}: {zone_name}")
    total = len(queued_zones) * minutes
    print(f"\n  Total run time: ~{total} minutes ({total / 60:.1f} hours)")
    print()


def display_irrigation_stop(zone_name, zone_num=None):
    if zone_name:
        print(f"\n\U0001f4a7 Stopped {zone_name} (Zone {zone_num})")
    else:
        print("\n\U0001f4a7 Stopped all zones")
    print()


def display_irrigation_suspend(until_str, zone_name, zone_num=None):
    if zone_name:
        print(f"\n\U0001f4a7 Suspended {zone_name} (Zone {zone_num}) until {until_str}")
    else:
        print(f"\n\U0001f4a7 Suspended all zones until {until_str}")
    print()


def display_irrigation_resume(zone_name, zone_num=None):
    if zone_name:
        print(f"\n\U0001f4a7 Resumed {zone_name} (Zone {zone_num})")
    else:
        print("\n\U0001f4a7 Resumed all zones")
    print()


def display_irrigation_history(entries, days):
    print(f"\n\U0001f4a7 Watering History \u2014 Last {days} Days")
    print("\u2500" * 60)

    if not entries:
        print("  No watering events recorded.")
    else:
        for e in entries:
            print(f"  {e['run_time']}  {e['zone_name']:<28} {e['duration']}  {e['status']}")
    print()


def display_treatments(rows, year):
    print(f"\n\U0001f331 Treatment History \u2014 {year}")
    print("\u2500" * 90)
    print(f"  {'Date':<12} {'Area':<16} {'Product':<32} {'Method':<16} {'Cost':>7}")
    print("\u2500" * 90)
    for r in rows:
        cost_str = f"${r['cost']:.2f}" if r["cost"] else "\u2014"
        print(
            f"  {r['date']:<12} {r['treatment_area']:<16} {r['product']:<32} {(r['method'] or _DASH):<16} {cost_str:>7}"
        )
    if not rows:
        print("  No treatments recorded.")
    print()


def display_products(rows, reorder_names=None):
    """Display product inventory. Optionally flag products needing reorder."""
    reorder_set = set(reorder_names or [])
    print("\n\U0001f4e6 Product Inventory")
    print("\u2500" * 94)
    print(f"  {'Product':<34} {'Category':<14} {'Qty':>5} {'Unit':<6} {'Cost':>8} {'Source':<12} {'':>4}")
    print("\u2500" * 94)
    for r in rows:
        cost_str = f"${r['cost_each']:.2f}" if r["cost_each"] else "\u2014"
        flag = " \u26a0\ufe0f" if r["name"] in reorder_set else ""
        print(
            f"  {r['name']:<34} {(r['category'] or _DASH):<14} {r['qty_on_hand']:>5.0f} {(r['unit'] or _DASH):<6} {cost_str:>8} {(r['source'] or _DASH):<12}{flag}"
        )
    if not rows:
        print("  No products in inventory.")
    if reorder_set:
        print("\n  \u26a0\ufe0f  = reorder needed (qty = 0)")
    print()


def display_reorder_alerts(alerts):
    """Display products needing reorder."""
    print("\n\u26a0\ufe0f  Reorder Alerts")
    print("\u2500" * 80)
    if not alerts:
        print("  All products in stock. No reorder needed.")
        print()
        return

    print(f"  {'Product':<34} {'Last Ordered':<14} {'Used':>5}  {'Source':<14}")
    print("\u2500" * 80)
    for a in alerts:
        last_ord = a["last_ordered"] or _DASH
        used_str = str(a["treatment_count"]) if a["treatment_count"] else "0"
        source = a["source"] or _DASH
        print(f"  {a['name']:<34} {last_ord:<14} {used_str:>5}x {source:<14}")
    print(f"\n  {len(alerts)} product(s) at zero stock.")
    print()


def display_mowing_summary(rows, total_visits, total_cost, year):
    print(f"\n\U0001f33f Mowing Summary \u2014 {year}")
    print("\u2500" * 60)
    for r in rows:
        cost_str = f"${r['cost']:.2f}" if r["cost"] else "\u2014"
        print(f"  {r['date']}  {(r['provider'] or _DASH):<24} {cost_str}")
    print("\u2500" * 60)
    print(f"  Total: {total_visits} visits, ${total_cost:.2f}")
    print()


def display_equipment(rows):
    print("\n\U0001f527 Equipment Inventory")
    print("\u2500" * 80)
    print(f"  {'Item':<32} {'Purchased':<12} {'Cost':>8} {'Source':<14} {'Status'}")
    print("\u2500" * 80)
    for r in rows:
        cost_str = f"${r['cost']:.2f}" if r["cost"] else "\u2014"
        print(
            f"  {r['name']:<32} {(r['purchase_date'] or _DASH):<12} {cost_str:>8} {(r['source'] or _DASH):<14} {(r['status'] or _DASH)}"
        )
    if not rows:
        print("  No equipment recorded.")
    print()


def display_spend_report(category_rows, grand_total, item_rows, year, category=None):
    print(f"\n\U0001f4b0 Spending Report \u2014 {year}")
    if category:
        print(f"   Category: {category}")
    print("\u2500" * 70)

    if category_rows:
        print("\n  By Category:")
        for r in category_rows:
            print(f"    {(r['category'] or 'uncategorized'):<20} {r['count']:>3} items    ${r['total']:>8.2f}")
        print("    " + "\u2500" * 45)
        print(f"    {'TOTAL':<20} {'':>3}          ${grand_total:>8.2f}")

    print(f"\n  {'Date':<12} {'Item':<30} {'Category':<14} {'Cost':>8} {'Source'}")
    print("  " + "\u2500" * 66)
    for r in item_rows:
        cost_str = f"${r['cost']:.2f}" if r["cost"] else "\u2014"
        print(
            f"  {r['date']:<12} {r['item']:<30} {(r['category'] or _DASH):<14} {cost_str:>8} {(r['source'] or _DASH)}"
        )
    if not item_rows:
        print("  No purchases recorded.")
    print()


def display_obsidian_import(counts):
    print("\n\U0001f4e5 Obsidian Import Complete")
    print("\u2500" * 40)
    for table, count in counts.items():
        print(f"  {table:<20} {count:>3} rows")
    print()


def display_ynab_preview(mowing_visits, purchases, skipped):
    print("\n\U0001f4b3 YNAB Import Preview")
    print("\u2500" * 70)

    if mowing_visits:
        print(f"\n  Mowing Visits ({len(mowing_visits)}):")
        print(f"  {'Date':<12} {'Provider':<30} {'Cost':>8}")
        print("  " + "\u2500" * 52)
        for m in mowing_visits:
            print(f"  {m['date']:<12} {m['provider']:<30} ${m['cost']:>7.2f}")

    if purchases:
        print(f"\n  Purchases ({len(purchases)}):")
        print(f"  {'Date':<12} {'Item':<28} {'Category':<12} {'Cost':>8} {'Source'}")
        print("  " + "\u2500" * 66)
        for p in purchases:
            item = p["item"][:27]
            print(f"  {p['date']:<12} {item:<28} {p['category']:<12} ${p['cost']:>7.2f} {p['source']}")

    if skipped:
        print(f"\n  Skipped ({len(skipped)}):")
        for s in skipped:
            print(f"  {s['date']}  {s['payee']} - {s['reason']}")

    total_cost = sum(m["cost"] for m in mowing_visits) + sum(p["cost"] for p in purchases)
    print(f"\n  Total: {len(mowing_visits)} mowing visits + {len(purchases)} purchases = ${total_cost:.2f}")
    print()


def display_ynab_import(mowing_count, purchase_count, skip_count):
    print("\n\U0001f4b3 YNAB Import Complete")
    print("\u2500" * 40)
    print(f"  Mowing visits:  {mowing_count:>3} added")
    print(f"  Purchases:      {purchase_count:>3} added")
    print(f"  Skipped:        {skip_count:>3} (duplicates or excluded)")
    print()


def display_recommend(result, config):
    """Display fertilizer recommendation."""
    phase_icons = {
        "Dormant": "\u2744\ufe0f",
        "Green-Up": "\U0001f331",
        "Active Growth": "\u2600\ufe0f",
        "Peak Summer": "\U0001f525",
        "Fall Prep": "\U0001f342",
        "Pre-Dormancy": "\U0001f319",
    }
    icon = phase_icons.get(result["phase"], "\U0001f33f")
    grass = result["grass_type"].title()

    print(f"\n\U0001f9ea Fertilizer Recommendation {_DASH} {config['location']['name']}")
    print("\u2500" * 60)
    print(f"  Grass:       {grass}")
    print(f"  Month:       {result['month_name']}")
    if result["soil_temp"] is not None:
        print(f"  Soil Temp:   {result['soil_temp']:.1f}\u00b0F avg / {result['soil_max']:.1f}\u00b0F max")
    print(f"  Phase:       {icon} {result['phase']}")
    print(f"\n  {result['summary']}")

    # Actions
    print("\n  Recommended Actions:")
    for action in result["actions"]:
        print(f"    \u2022 {action}")

    # Product recommendations
    if result["products"]:
        print("\n  Products to Use:")
        for p in result["products"]:
            priority_icon = {
                "high": "\U0001f534",
                "medium": "\U0001f7e1",
                "low": "\u26aa",
            }.get(p.get("priority", ""), "  ")
            cat = p["category"].title()
            npk = f" ({p['npk_focus']})" if p.get("npk_focus") else ""
            print(f"    {priority_icon} {cat}{npk}")
            if p.get("note"):
                print(f"       {p['note']}")

    # Treatment history
    if result["last_fertilizer"]:
        days = result["days_since_fertilizer"]
        days_str = f"{days} days ago" if days is not None else "unknown"
        print(f"\n  Last Fertilizer: {days_str}")
        for t in result["last_fertilizer"][:3]:
            print(f"    {t['date']}  {t['product']} ({t['area']})")
    else:
        print("\n  Last Fertilizer: No records found")

    if result["last_pre_emergent"]:
        days = result["days_since_pre_emergent"]
        days_str = f"{days} days ago" if days is not None else "unknown"
        print(f"\n  Last Pre-Emergent: {days_str}")
        for t in result["last_pre_emergent"][:2]:
            print(f"    {t['date']}  {t['product']} ({t['area']})")

    # Inventory check
    if result["inventory"]:
        print("\n  Inventory:")
        for item in result["inventory"]:
            qty_str = f"{item['qty']:.0f}" if item["qty"] == int(item["qty"]) else f"{item['qty']:.1f}"
            status = "\u2705" if item["qty"] > 0 else "\u274c OUT"
            print(f"    {status} {item['name']}: {qty_str} {item['unit']}")

    # Context-aware notes
    if result["context_notes"]:
        print("\n  \u26a0\ufe0f  Notes:")
        for note in result["context_notes"]:
            print(f"    \u2022 {note}")

    print()


def display_water_usage(result):
    """Display water usage correlation report."""
    year = result["year"]
    baseline = result["baseline_avg"]
    months = result["months"]

    print(f"\n💧 Water Usage Report - {year}")
    print("─" * 75)

    if baseline is not None:
        bl_months = ", ".join(m[-2:] for m in result["baseline_months"][-6:])
        print(f"  Baseline: ${baseline:,.2f}/mo (avg of months with no irrigation: {bl_months})")
    else:
        print("  Baseline: unknown (no months with zero irrigation and a water bill)")

    print()
    print(f"  {'Month':<10} {'Irr Min':>8} {'Runs':>5} {'Water Bill':>11} {'Est. Irr $':>11} {'$/Min':>7}")
    print("  " + "─" * 63)

    for m in months:
        month = m["month"]
        irr_min = f"{m['irrigation_minutes']:>7.1f}" if m["irrigation_minutes"] else "      -"
        runs = f"{m['irrigation_runs']:>4}" if m["irrigation_runs"] else "   -"
        bill = f"${m['water_bill']:>9,.2f}" if m["water_bill"] is not None else "        -"

        if m["estimated_irrigation_cost"] is not None:
            est = f"${m['estimated_irrigation_cost']:>9,.2f}"
            cpm = f"${m['cost_per_minute']:.2f}" if m["cost_per_minute"] else "    -"
        elif m["is_baseline"]:
            est = "  baseline"
            cpm = "     -"
        else:
            est = "        -"
            cpm = "     -"

        print(f"  {month:<10} {irr_min} {runs} {bill} {est} {cpm:>7}")

    print("  " + "─" * 63)

    total_min = result["total_irrigation_minutes"]
    total_cost = result["total_estimated_irrigation_cost"]
    avg_cpm = result["avg_cost_per_minute"]

    print(f"  Total irrigation: {total_min:,.1f} min")
    if total_cost > 0:
        print(f"  Est. irrigation cost: ${total_cost:,.2f}")
    if avg_cpm:
        print(f"  Avg cost/min: ${avg_cpm:.4f}")

    print()


def display_irrigation_pace(result):
    """Display monthly irrigation pace tracker."""
    alert_icons = {"on-track": "\u2705", "elevated": "\u26a0\ufe0f ", "high": "\U0001f534"}
    icon = alert_icons.get(result["alert_level"], "")

    print(f"\n\U0001f4ca Irrigation Pace {_DASH} {result['month_name']}")
    print("\u2500" * 60)
    print(
        f"  Day {result['day_of_month']} of {result['days_in_month']} ({result['pct_through_month']:.0f}% through month)"
    )
    print()
    print(f"  Current:     {result['current_minutes']:.1f} min ({result['current_runs']} runs)")
    print(f"  Daily rate:  {result['daily_rate_minutes']:.1f} min/day")
    print(f"  Projected:   {result['projected_minutes']:.0f} min (${result['projected_cost']:.2f})")
    print(f"  Alert:       {icon} {result['alert_level'].upper()}")
    print()
    print(f"  Last year:   {result['last_year_minutes']:.0f} min ({result['last_year_runs']} runs)")

    if result["historical_avg_minutes"] is not None:
        print(f"  Hist. avg:   {result['historical_avg_minutes']:.0f} min")

    thresholds = result["thresholds"]
    print(f"\n  Cost rate:   ${thresholds['cost_per_minute']:.2f}/min")
    print(f"  Thresholds:  elevated >${thresholds['elevated_cost']:.0f}  high >${thresholds['high_cost']:.0f}")
    print()


def display_irrigation_budget(result):
    """Display irrigation budget tracking."""
    status_icons = {
        "no_budget": "\u2796",
        "ok": "\u2705",
        "warning": "\u26a0\ufe0f ",
        "over": "\U0001f534",
    }
    icon = status_icons.get(result["budget_status"], "")

    print(f"\n\U0001f4b0 Irrigation Budget {_DASH} {result['month_name']}")
    print("\u2500" * 60)

    if not result["budget_set"]:
        print(f"  Status: {icon} No budget configured")
        print(f"\n  Current:     {result['current_minutes']:.1f} min (${result['current_cost']:.2f})")
        print(f"  Projected:   {result['projected_minutes']:.0f} min (${result['projected_cost']:.2f})")
        print("\n  Set a budget in config.yaml under hydrawise.budget:")
        print("    monthly_minutes: 600   # or")
        print("    monthly_dollars: 150")
    else:
        print(f"  Status: {icon} {result['budget_status'].upper()}")
        if result["monthly_minutes_limit"] is not None:
            print(f"  Budget:      {result['monthly_minutes_limit']} min/month")
        elif result["monthly_dollars_limit"] is not None:
            print(f"  Budget:      ${result['monthly_dollars_limit']:.2f}/month")
        print(f"  Current:     {result['current_minutes']:.1f} min (${result['current_cost']:.2f})")
        if result["pct_used"] is not None:
            bar_width = 30
            filled = min(bar_width, int(result["pct_used"] / 100 * bar_width))
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            print(f"  Used:        {bar} {result['pct_used']:.0f}%")
        if result["remaining"] is not None:
            print(f"  Remaining:   {result['remaining']}")
        print(f"  Projected:   {result['projected_minutes']:.0f} min (${result['projected_cost']:.2f})")

    if result["recommendations"]:
        print("\n  Recommendations:")
        for r in result["recommendations"]:
            print(f"    \u2022 {r}")

    ynab = result.get("ynab_integration")
    if ynab:
        print("\n  YNAB Integration:")
        if ynab.get("estimated_water_bill") is not None:
            print(f"    Est. water bill:  ${ynab['estimated_water_bill']:.2f}")
        if ynab.get("last_bill_amount") is not None:
            print(f"    Last bill:        ${ynab['last_bill_amount']:.2f} ({ynab['last_bill_month']})")
        print(f"    Category:         {ynab['category']}")

    print()


def display_zone_analysis(result):
    """Display per-zone irrigation breakdown."""
    period = result["period"]

    print(f"\n\U0001f4a7 Zone Analysis {_DASH} {period}")
    print("\u2500" * 80)

    if not result["zones"]:
        print("  No irrigation data available.")
        print()
        return

    # Program summary
    if result["programs"]:
        print("\n  Program Summary:")
        for p in result["programs"]:
            print(
                f"    {p['name']:<12} {p['zone_count']} zones  {p['total_minutes']:>8.0f} min total  "
                f"{p['avg_minutes_per_zone']:>6.0f} min avg/zone"
            )

    # Zone details
    print(f"\n  {'Zone':<6} {'Name':<28} {'Program':<10} {'Total':>8} {'Runs':>5} {'Avg':>6} {'Min':>5} {'Max':>5}")
    print("  " + "\u2500" * 76)
    for z in result["zones"]:
        prog = z.get("program", _DASH)
        total = f"{z['total_minutes']:.0f}m"
        avg = f"{z['avg_duration']:.0f}m"
        mn = f"{z['min_duration']:.0f}m" if z.get("min_duration") is not None else _DASH
        mx = f"{z['max_duration']:.0f}m" if z.get("max_duration") is not None else _DASH
        note = z.get("note", "")
        flag_mark = " \u26a0" if any(f["zone_number"] == z["zone_number"] for f in result["flags"]) else ""
        print(
            f"  {z['zone_number']:<6} {z['zone_name']:<28} {prog:<10} {total:>8} {z['run_count']:>5} {avg:>6} {mn:>5} {mx:>5}{flag_mark}"
        )
        if note:
            print(f"         {note}")

    # Flags
    if result["flags"]:
        print("\n  \u26a0\ufe0f  Flags:")
        for f in result["flags"]:
            print(f"    Zone {f['zone_number']} ({f['zone_name']}): {f['detail']}")

    print()


def display_et_recommendations(result):
    """Display ET% reduction recommendations."""
    print(f"\n\U0001f4b0 ET Recommendations {_DASH} {result['year']}")
    print("\u2500" * 75)

    if not result["months"]:
        print(f"  {result.get('note', 'No data available.')}")
        print()
        return

    avg_cpm = result["avg_cost_per_minute"]
    baseline = result["baseline_avg"]

    if baseline is not None:
        print(f"  Water baseline: ${baseline:.2f}/mo")
    if avg_cpm is not None:
        print(f"  Avg cost/min:   ${avg_cpm:.4f}")
    print()

    print(f"  {'Month':<10} {'Irr Min':>8} {'Irr Cost':>10} {'$/Min':>8} {'Ratio':>6} {'Level'}")
    print("  " + "\u2500" * 55)

    for m in result["months"]:
        level_icon = {
            "normal": " ",
            "elevated": "\u26a0",
            "high": "\U0001f534",
        }.get(m["expense_level"], " ")
        irr_cost = f"${m['irrigation_cost']:.2f}" if m["irrigation_cost"] is not None else _DASH
        cpm = f"${m['cost_per_minute']:.4f}" if m["cost_per_minute"] else _DASH
        ratio = f"{m['cpm_ratio']:.1f}x"
        print(f"  {m['month']:<10} {m['irrigation_minutes']:>8.0f} {irr_cost:>10} {cpm:>8} {ratio:>6} {level_icon}")

    if result["recommendations"]:
        print("\n  Recommendations:")
        for r in result["recommendations"]:
            print(f"    \u2022 {r['detail']}")
        print(f"\n  Potential annual savings: ${result['potential_savings']:.2f}")

    if result.get("note"):
        print(f"\n  Note: {result['note']}")

    print()


def display_reminder_created(result):
    """Display confirmation of a created Apple Reminder."""
    print("\n\u23f0 Reminder Created")
    print("\u2500" * 50)
    print(f"  Title:  {result['title']}")
    print(f"  Date:   {result['date']}")
    print(f"  Time:   {result['time']}")
    print(f"  List:   {result['list']}")
    if result.get("notes"):
        print(f"  Notes:  {result['notes']}")
    print()


def display_reminders_list(result):
    """Display reminders from Apple Reminders."""
    reminders = result["reminders"]
    count = result["count"]
    list_name = result["list"]

    print(f"\n⏰ Reminders - {list_name} ({count} item{'s' if count != 1 else ''})")
    print("─" * 70)

    if not reminders:
        print("  No reminders found.")
        print()
        return

    for r in reminders:
        status = "✅" if r["completed"] else "⬜"
        due = r["due_date"] or "no date"
        print(f"  {status} {r['title']}")
        print(f"     Due: {due}")
        if r["notes"]:
            # Truncate long notes
            notes = r["notes"][:80] + ("…" if len(r["notes"]) > 80 else "")
            print(f"     Notes: {notes}")
        print(f"     ID: {r['id']}")
        print()


def display_reminder_list_config(rem_config):
    """Display the configured Apple Reminders list."""
    print("\n\u23f0 Reminders Configuration")
    print("\u2500" * 50)
    print(f"  List:          {rem_config['list']}")
    print(f"  Default time:  {rem_config['default_time']}")
    print()
