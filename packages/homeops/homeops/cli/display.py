"""Terminal output formatting for HomeOps."""

_DASH = "\u2014"


def print_task_list(tasks):
    """Print task list with status indicators."""
    if not tasks:
        print("No tasks defined. Use 'homeops task add' to create one.")
        return

    print(f"\n{'Task':<35} {'Category':<12} {'Interval':<10} {'Next Due':<12} {'Status'}")
    print("-" * 90)

    for t in tasks:
        interval = _format_interval(t["interval_days"])
        next_due = t["next_due"] or "never done"
        if t["overdue"]:
            status = f"OVERDUE ({abs(t['days_until'])}d)"
        elif t["days_until"] is not None:
            if t["days_until"] <= 7:
                status = f"due soon ({t['days_until']}d)"
            else:
                status = f"OK ({t['days_until']}d)"
        else:
            status = "not started"

        # Highlight overdue
        marker = "!" if t["overdue"] else " "
        print(f"{marker} {t['name']:<34} {t['category']:<12} {interval:<10} {next_due:<12} {status}")

    print()


def print_overdue(tasks):
    """Print overdue tasks only."""
    if not tasks:
        print("\nNo overdue tasks.")
        return

    print(f"\nOVERDUE TASKS ({len(tasks)})")
    print("-" * 70)
    for t in tasks:
        days = abs(t["days_until"])
        print(f"  ! {t['name']:<30} {t['category']:<12} {days}d overdue (was due {t['next_due']})")
    print()


def print_task_done(result):
    """Print task completion confirmation."""
    print(f"\n✅ Task completed: {result['name']}")
    print(f"  Done:     {result['done_date']}")
    print(f"  Next due: {result['next_due']}")
    if result.get("cost"):
        print(f"  Cost:     ${result['cost']:.2f}")
    if result.get("provider"):
        print(f"  Provider: {result['provider']}")
    print()


def print_task_added(result):
    """Print task creation confirmation."""
    interval = _format_interval(result["interval_days"])
    print(f"\nTask added: {result['name']}")
    print(f"  Category: {result['category']}")
    print(f"  Interval: {interval}")
    print()


def print_task_history(history):
    """Print completion history for a task."""
    if not history:
        print("\nNo completion history found.")
        return

    name = history[0]["task_name"]
    print(f"\nHistory: {name}")
    print(f"{'Date':<12} {'Cost':>8} {'Provider':<25} {'Notes'}")
    print("-" * 70)

    for h in history:
        cost = f"${h['cost']:.2f}" if h.get("cost") else _DASH
        provider = h.get("provider") or _DASH
        notes = h.get("notes") or ""
        print(f"  {h['date']:<12} {cost:>8} {provider:<25} {notes}")
    print()


def print_pest_history(treatments):
    """Print pest treatment history."""
    if not treatments:
        print("\nNo pest treatments logged.")
        return

    print(f"\n{'Date':<12} {'Area':<15} {'Product':<25} {'Method':<10} {'Cost':>8} {'Notes'}")
    print("-" * 90)

    for t in treatments:
        cost = f"${t['cost']:.2f}" if t.get("cost") else _DASH
        method = t.get("method") or _DASH
        notes = t.get("notes") or ""
        print(f"  {t['date']:<12} {t['area']:<15} {t['product']:<25} {method:<10} {cost:>8} {notes}")
    print()


def print_pest_added(result):
    """Print pest treatment confirmation."""
    print(f"\nTreatment logged: {result['product']} - {result['area']}")
    print(f"  Date: {result['date']}")
    if result.get("cost"):
        print(f"  Cost: ${result['cost']:.2f}")
    print()


def print_provider_list(providers):
    """Print provider directory."""
    if not providers:
        print("\nNo providers registered. Use 'homeops provider add' to add one.")
        return

    print(f"\n{'Name':<30} {'Category':<12} {'Phone':<16} {'Typical Cost':>13} {'Notes'}")
    print("-" * 90)

    current_cat = None
    for p in providers:
        if p["category"] != current_cat:
            current_cat = p["category"]
        cost = f"${p['typical_cost']:.2f}" if p.get("typical_cost") else _DASH
        phone = p.get("phone") or _DASH
        notes = p.get("notes") or ""
        print(f"  {p['name']:<30} {p['category']:<12} {phone:<16} {cost:>13} {notes}")
    print()


def print_provider_added(result):
    """Print provider creation confirmation."""
    print(f"\nProvider added: {result['name']} ({result['category']})")
    print()


def print_provider_detail(provider):
    """Print detailed provider view with cost history."""
    if not provider:
        print("\nProvider not found.")
        return

    print(f"\n{provider['name']}")
    print(f"  Category: {provider['category']}")
    print(f"  Phone:    {provider.get('phone') or _DASH}")
    print(f"  Email:    {provider.get('email') or _DASH}")
    print(f"  Typical:  ${provider['typical_cost']:.2f}" if provider.get("typical_cost") else f"  Typical:  {_DASH}")
    if provider.get("notes"):
        print(f"  Notes:    {provider['notes']}")

    history = provider.get("cost_history", [])
    if history:
        print(f"\n  Cost History ({len(history)} entries)")
        print(f"  {'Date':<12} {'Amount':>8} {'Description'}")
        print(f"  {'-' * 50}")
        for h in history:
            print(f"  {h['date']:<12} ${h['amount']:>7.2f} {h.get('description') or ''}")
    print()


def print_cost_summary(summary, year=None):
    """Print cost summary by category."""
    if not summary:
        label = f" ({year})" if year else ""
        print(f"\nNo costs recorded{label}.")
        return

    label = f" ({year})" if year else " (all time)"
    total = sum(s["total"] for s in summary)

    print(f"\nCost Summary{label}")
    print(f"{'Category':<15} {'Count':>6} {'Total':>10} {'Average':>10}")
    print("-" * 50)

    for s in summary:
        print(f"  {s['category']:<15} {s['count']:>6} ${s['total']:>9.2f} ${s['avg']:>9.2f}")

    print("-" * 50)
    print(f"  {'TOTAL':<15} {'':>6} ${total:>9.2f}")
    print()


def print_cost_history(costs):
    """Print cost line items."""
    if not costs:
        print("\nNo cost entries found.")
        return

    print(f"\n{'Date':<12} {'Category':<12} {'Amount':>9} {'Provider':<20} {'Description'}")
    print("-" * 80)

    for c in costs:
        provider = c.get("provider") or _DASH
        desc = c.get("description") or ""
        print(f"  {c['date']:<12} {c['category']:<12} ${c['amount']:>8.2f} {provider:<20} {desc}")
    print()


def print_status(data):
    """Print comprehensive status dashboard."""
    print(f"\n{'=' * 60}")
    print(f"  HOMEOPS STATUS - {data['date']}")
    print(f"{'=' * 60}")

    # Tasks
    tasks = data["tasks"]
    print(f"\n  Tasks: {tasks['total']} active", end="")
    if tasks["overdue_count"]:
        print(f" | {tasks['overdue_count']} OVERDUE", end="")
    if tasks["due_soon_count"]:
        print(f" | {tasks['due_soon_count']} due soon", end="")
    print()

    if tasks["overdue"]:
        for t in tasks["overdue"]:
            print(f"    ! {t['name']} - {abs(t['days_until'])}d overdue")
    if tasks["due_soon"]:
        for t in tasks["due_soon"]:
            print(f"    > {t['name']} - {t['days_until']}d")

    # Appliances
    app = data["appliances"]
    print(f"\n  Appliances: {app['total']} tracked", end="")
    if app["expiring_count"]:
        print(f" | {app['expiring_count']} warranty expiring", end="")
    if app["aging_count"]:
        print(f" | {app['aging_count']} aging", end="")
    print()

    # Pest
    pest = data["pest"]
    if pest["recent_treatments"]:
        last = pest["recent_treatments"][0]
        print(f"\n  Last pest treatment: {last['date']} - {last['product']} ({last['area']})")

    # Costs
    costs = data["costs"]
    print(f"\n  YTD spending: ${costs['ytd_total']:.2f}")
    if costs["by_category"]:
        for c in costs["by_category"][:5]:
            print(f"    {c['category']}: ${c['total']:.2f}")

    # Providers
    print(f"\n  Providers: {data['providers']['total']} registered")

    print(f"\n{'=' * 60}\n")


def print_checklist_list(checklists):
    """Print available checklists."""
    print("\nAvailable Seasonal Checklists")
    print("-" * 50)
    for key, cl in checklists.items():
        print(f"  {key:<10} {cl['description']} ({len(cl['tasks'])} tasks)")
    print("\nUsage: homeops checklist show <season>")
    print("       homeops checklist load <season>\n")


def print_checklist_preview(checklist):
    """Print checklist contents without loading."""
    print(f"\n{checklist['name']} Checklist - {checklist['description']}")
    print("-" * 60)
    for t in checklist["tasks"]:
        interval = _format_interval(t["interval_days"])
        print(f"  [{t['category']:<10}] {t['name']} ({interval})")
        if t.get("notes"):
            print(f"               {t['notes']}")
    print(f"\nTo load these tasks: homeops checklist load {checklist['name'].lower()}\n")


def print_checklist_loaded(result):
    """Print checklist load results."""
    print(f"\n{result['season']} Checklist Loaded")
    print("-" * 50)
    if result["added"]:
        print(f"  Added ({result['added_count']}):")
        for name in result["added"]:
            print(f"    + {name}")
    if result["skipped"]:
        print(f"  Skipped ({result['skipped_count']}) - already exist:")
        for name in result["skipped"]:
            print(f"    = {name}")
    print()


def print_sinking_fund_plan(data):
    """Print appliance sinking fund recommendations."""
    plans = data["plans"]
    if not plans:
        print("\nNo appliances with replacement cost data.")
        return

    print("\nAppliance Replacement Sinking Fund Plan")
    print(f"{'Appliance':<35} {'Replace $':>10} {'Years Left':>10} {'$/Month':>8} {'Urgency'}")
    print("-" * 85)

    for p in plans:
        remaining = f"{p['remaining_years']}y" if p["remaining_years"] > 0 else "NOW"
        print(
            f"  {p['name']:<35} ${p['replacement_cost']:>9,.0f} {remaining:>10} ${p['monthly_savings']:>7.2f} {p['urgency']}"
        )

    print("-" * 85)
    print(f"  {'TOTAL monthly savings needed':<35} {'':>10} {'':>10} ${data['total_monthly_savings']:>7.2f}")
    print(f"  {'TOTAL annual savings needed':<35} {'':>10} {'':>10} ${data['total_annual_savings']:>7.2f}")
    print()


def print_upcoming_maintenance(data):
    """Print upcoming maintenance costs."""
    upcoming = data["upcoming"]
    if not upcoming:
        print("\nNo maintenance tasks due within 90 days.")
        return

    print("\nUpcoming Maintenance (Next 90 Days)")
    print(f"{'Task':<35} {'Category':<12} {'Due':>12} {'Status'}")
    print("-" * 75)

    for u in upcoming:
        if u["overdue"]:
            status = f"OVERDUE ({abs(u['days_until'])}d)"
        else:
            status = f"in {u['days_until']}d"
        print(f"  {u['name']:<35} {u['category']:<12} {u['next_due']:>12} {status}")
    print()


def print_utility_budget_rec(data):
    """Print utility budget recommendations."""
    recs = data.get("recommendations", [])
    if not recs:
        print(f"\n{data.get('note', 'No utility data available.')}")
        return

    print("\nUtility Budget Recommendations (avg + 10% buffer)")
    print(f"{'Type':<12} {'Average':>9} {'Min':>8} {'Max':>8} {'Recommended':>12} {'Data'}")
    print("-" * 65)

    for r in recs:
        print(
            f"  {r['type']:<12} ${r['avg_monthly']:>8.2f} ${r['min_monthly']:>7.2f} ${r['max_monthly']:>7.2f} ${r['recommended_budget']:>11.2f} {r['months_of_data']}mo"
        )

    print("-" * 65)
    print(f"  {'TOTAL':<12} {'':>9} {'':>8} {'':>8} ${data['total_recommended_monthly']:>11.2f}")
    print()


def print_budget_overview(data):
    """Print comprehensive budget overview."""
    print("\n" + "=" * 70)
    print("  HOMEOPS BUDGET PLANNING OVERVIEW")
    print("=" * 70)

    # Sinking funds
    sinking = data["appliance_sinking_funds"]
    if sinking["plans"]:
        print(f"\n  Appliance Replacement Fund: ${sinking['total_monthly_savings']:.2f}/mo needed")
        for p in sinking["plans"]:
            if p["urgency"] in ("OVERDUE", "HIGH"):
                print(
                    f"    ! {p['name']}: ${p['replacement_cost']:,.0f} in {p['remaining_years']}y - ${p['monthly_savings']:.2f}/mo"
                )

    # Upcoming maintenance
    maint = data["upcoming_maintenance"]
    if maint["upcoming"]:
        print(f"\n  Upcoming Maintenance ({maint['count']} tasks in next 90 days)")
        for u in maint["upcoming"]:
            marker = "!" if u["overdue"] else " "
            status = f"OVERDUE {abs(u['days_until'])}d" if u["overdue"] else f"in {u['days_until']}d"
            print(f"   {marker} {u['name']}: {status}")

    # Utilities
    utils = data["utility_recommendations"]
    recs = utils.get("recommendations", [])
    if recs:
        print(f"\n  Utility Budget: ${utils['total_recommended_monthly']:.2f}/mo recommended")
        for r in recs:
            print(f"    {r['type']}: ${r['recommended_budget']:.2f}/mo (avg ${r['avg_monthly']:.2f})")

    # YTD spending
    ytd = data["ytd_spending"]
    if ytd["total"] > 0:
        print(f"\n  YTD Home Maintenance Spending: ${ytd['total']:.2f}")
        for c in ytd["by_category"]:
            print(f"    {c['category']}: ${c['total']:.2f}")

    print("\n" + "=" * 70)
    print()


def print_utility_added(result):
    """Print utility bill confirmation."""
    print(f"\nUtility bill logged: {result['type']} - {result['date']}")
    print(f"  Amount: ${result['amount']:.2f}")
    if result.get("usage"):
        print(f"  Usage:  {result['usage']}")
    print()


def print_utility_trend(bills, utility_type):
    """Print monthly trend for a utility type."""
    if not bills:
        print(f"\nNo {utility_type} bills recorded.")
        return

    print(f"\n{utility_type.title()} Bill Trend")
    print(f"{'Month':<10} {'Amount':>9} {'Usage':<20}")
    print("-" * 45)

    total = 0
    for b in bills:
        usage = b.get("usage") or _DASH
        print(f"  {b['bill_date']:<10} ${b['amount']:>8.2f} {usage:<20}")
        total += b["amount"]

    avg = total / len(bills) if bills else 0
    print("-" * 45)
    print(f"  {'Average':<10} ${avg:>8.2f}")
    print(f"  {'Total':<10} ${total:>8.2f} ({len(bills)} months)")
    print()


def print_utility_summary(summary, year=None):
    """Print utility spending summary."""
    if not summary:
        label = f" ({year})" if year else ""
        print(f"\nNo utility bills recorded{label}.")
        return

    label = f" ({year})" if year else " (all time)"
    total = sum(s["total"] for s in summary)

    print(f"\nUtility Summary{label}")
    print(f"{'Type':<12} {'Months':>6} {'Total':>10} {'Average':>10} {'Min':>8} {'Max':>8}")
    print("-" * 60)

    for s in summary:
        print(
            f"  {s['type']:<12} {s['months']:>6} ${s['total']:>9.2f} ${s['avg']:>9.2f} ${s['min']:>7.2f} ${s['max']:>7.2f}"
        )

    print("-" * 60)
    print(f"  {'TOTAL':<12} {'':>6} ${total:>9.2f}")
    print()


def print_utility_history(bills):
    """Print all utility bills."""
    if not bills:
        print("\nNo utility bills recorded.")
        return

    print(f"\n{'Month':<10} {'Type':<12} {'Amount':>9} {'Usage':<20}")
    print("-" * 55)

    for b in bills:
        usage = b.get("usage") or _DASH
        print(f"  {b['bill_date']:<10} {b['type']:<12} ${b['amount']:>8.2f} {usage:<20}")
    print()


def print_appliance_added(result):
    """Print appliance creation confirmation."""
    print(f"\nAppliance added: {result['name']} ({result['category']})")
    if result.get("brand"):
        print(f"  Brand: {result['brand']}")
    print()


def print_appliance_list(appliances):
    """Print appliance registry."""
    if not appliances:
        print("\nNo appliances registered. Use 'homeops appliance add' to add one.")
        return

    print(f"\n{'Name':<30} {'Brand':<15} {'Age':>5} {'Warranty':>10} {'Life Left':>10} {'Replace $':>10}")
    print("-" * 95)

    for a in appliances:
        age = f"{a['age_years']}y" if a.get("age_years") is not None else _DASH
        if a.get("warranty_active") is True:
            warranty = f"{a['warranty_days_left']}d left"
        elif a.get("warranty_active") is False:
            warranty = "expired"
        else:
            warranty = _DASH
        life = f"{a['remaining_lifespan_years']}y" if a.get("remaining_lifespan_years") is not None else _DASH
        cost = f"${a['replacement_cost']:,.0f}" if a.get("replacement_cost") else _DASH
        print(f"  {a['name']:<30} {(a.get('brand') or _DASH):<15} {age:>5} {warranty:>10} {life:>10} {cost:>10}")

    print()


def print_appliance_expiring(appliances):
    """Print appliances with expiring warranties."""
    if not appliances:
        print("\nNo warranties expiring within 12 months.")
        return

    print(f"\nWarranties Expiring Soon ({len(appliances)})")
    print("-" * 60)
    for a in appliances:
        print(f"  {a['name']:<30} {a['warranty_days_left']}d left (ends {a.get('warranty_end', '?')})")
    print()


def print_appliance_aging(appliances):
    """Print appliances nearing end of life."""
    if not appliances:
        print("\nNo appliances within 2 years of expected end of life.")
        return

    print(f"\nAging Appliances ({len(appliances)})")
    print("-" * 70)
    for a in appliances:
        cost = f"${a['replacement_cost']:,.0f}" if a.get("replacement_cost") else "unknown"
        print(f"  {a['name']:<30} {a['remaining_lifespan_years']}y remaining  (replacement: {cost})")
    print()


def print_reminder_created(result):
    """Print reminder creation confirmation."""
    print("\n⏰ Reminder Created")
    print(f"{'─' * 50}")
    print(f"  Title:  {result['title']}")
    print(f"  Date:   {result['date']}")
    print(f"  Time:   {result['time']}")
    print(f"  List:   {result['list']}")
    if result.get("notes"):
        print(f"  Notes:  {result['notes']}")
    print()


def print_reminder_list(data):
    """Print reminder list."""
    reminders = data["reminders"]
    if not reminders:
        print(f"\nNo reminders in '{data['list']}'.")
        return

    print(f"\nReminders - {data['list']} ({data['count']})")
    print(f"{'Due':<18} {'Title':<50} {'Notes'}")
    print("-" * 90)

    for r in reminders:
        due = r["due_date"] or "no date"
        completed = " ✓" if r["completed"] else ""
        notes = r.get("notes", "")
        if len(notes) > 30:
            notes = notes[:27] + "..."
        print(f"  {due:<18} {r['title']:<50}{completed} {notes}")
    print()


def print_db_init(db_path):
    """Print database initialization confirmation."""
    print(f"\nDatabase initialized: {db_path}\n")


def print_log_export(counts, preview):
    """Print home_log markdown export row counts (issue #58)."""
    label = "Home Log Export Preview (no files written)" if preview else "Home Log Export Complete"
    print(f"\n{label}")
    print("-" * 40)
    for entity, count in counts.items():
        print(f"  {entity:<20} {count:>3} rows")
    print()


def print_hvac_snapshot_dry_run(data):
    """Print what would be captured without writing to DB."""
    outdoor = data.get("outdoor", {})
    print(f"\nHVAC Snapshot (dry run) - outdoor {outdoor.get('temp')}°F / {outdoor.get('humidity')}% humidity")
    print("-" * 65)
    for z in data.get("zones", []):
        # The dual-setpoint (target_low/target_high) branch below is
        # permanently unreachable as of issue #143: hvac_status() now
        # sources from Prometheus, which has no dual-setpoint metric, so
        # target_low/target_high are always None. Left in place (not dead
        # code to delete) in case a future data source restores that field.
        setpoint = (
            f"{z.get('target_low')}–{z.get('target_high')}°F"
            if z.get("target_low") and z.get("target_high")
            else f"{z.get('target_temp')}°F"
            if z.get("target_temp")
            else "-"
        )
        print(
            f"  {z['name']:<20} {z['mode']:<12} {z.get('current_temp')}°F  setpoint {setpoint}  humidity {z.get('humidity')}%"
        )
    print()


def print_hvac_snapshot_done(result, data):
    """Print snapshot capture confirmation."""
    outdoor = data.get("outdoor", {})
    print(f"\nHVAC snapshot captured - {result['captured_at']}")
    print(
        f"  Zones recorded: {result['zones_recorded']}  |  Outdoor: {outdoor.get('temp')}°F / {outdoor.get('humidity')}% humidity"
    )
    print()


def print_hvac_trend(rows, days, zone_filter=None):
    """Print hourly HVAC trend data."""
    label = f"zone: {zone_filter}" if zone_filter else "all zones"
    print(f"\nHVAC Trend - last {days} days ({label})")
    if not rows:
        print("  No snapshot data yet. Run 'homeops hvac snapshot' to start collecting.\n")
        return
    print(f"  {'Hour':<17} {'Zone':<18} {'Avg Temp':>9} {'Outdoor':>9} {'Humidity':>10} {'Active Min':>11}")
    print("  " + "-" * 76)
    for r in rows:
        print(
            f"  {r['hour']:<17} {r['zone']:<18} "
            f"{str(r['avg_temp']) + '°F':>9} "
            f"{str(r['avg_outdoor_temp']) + '°F':>9} "
            f"{str(r['max_humidity']) + '%':>10} "
            f"{r['active_minutes']:>11}"
        )
    print()


def print_hvac_modes(rows, days, zone_filter=None):
    """Print HVAC mode distribution."""
    label = f"zone: {zone_filter}" if zone_filter else "all zones"
    print(f"\nHVAC Mode Distribution - last {days} days ({label})")
    if not rows:
        print("  No snapshot data yet. Run 'homeops hvac snapshot' to start collecting.\n")
        return
    print(f"  {'Zone':<20} {'Mode':<12} {'Snapshots':>10} {'%':>6} {'Est Minutes':>12}")
    print("  " + "-" * 62)
    current_zone = None
    for r in rows:
        if r["zone"] != current_zone:
            if current_zone is not None:
                print()
            current_zone = r["zone"]
        print(
            f"  {r['zone']:<20} {r['hvac_mode']:<12} "
            f"{r['snapshot_count']:>10} {r['pct']:>5}% {r['estimated_minutes']:>12}"
        )
    print()


def print_hvac_efficiency(rows, months):
    """Print HVAC runtime vs electric bill correlation."""
    print(f"\nHVAC Efficiency - last {months} months")
    if not rows:
        print("  No snapshot data yet. Run 'homeops hvac snapshot' to start collecting.\n")
        return
    print(f"  {'Month':<10} {'Zone':<20} {'Active Min':>11} {'Avg Outdoor':>12} {'Electric Bill':>14}")
    print("  " + "-" * 70)
    current_month = None
    for r in rows:
        if r["month"] != current_month:
            if current_month is not None:
                print()
            current_month = r["month"]
        bill = f"${r['electric_bill']:.2f}" if r.get("electric_bill") else "-"
        outdoor = f"{r['avg_outdoor_temp']}°F" if r.get("avg_outdoor_temp") else "-"
        print(f"  {r['month']:<10} {r['zone']:<20} {r['active_minutes']:>11} {outdoor:>12} {bill:>14}")
    print()


def _format_interval(days):
    """Format interval days as human-readable string."""
    if days >= 365 and days % 365 == 0:
        years = days // 365
        return f"{years}y"
    elif days >= 30 and days % 30 == 0:
        months = days // 30
        return f"{months}m"
    return f"{days}d"
