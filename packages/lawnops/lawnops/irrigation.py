"""Hydrawise irrigation controller integration.

Async internals with sync public API. No sys.exit() - raises exceptions instead.
"""

import asyncio
from datetime import datetime, timedelta

from pydrawise import Auth as HydrawiseAuth
from pydrawise import Hydrawise


def run_async(coro):
    """Run an async coroutine synchronously.

    Uses asyncio.run() when no event loop is running (CLI).
    Falls back to loop.run_until_complete() when called from an
    existing loop context (MCP/FastMCP).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in an event loop - run in a new thread to avoid blocking
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


_client = None
_client_credentials = None


def get_client():
    """Create (or reuse) an authenticated Hydrawise client.

    pydrawise's own Auth class already caches/refreshes its OAuth token
    internally (auth.py's Token/check_token), but every top-level function
    in this module used to call get_client() fresh and discard the Auth
    instance afterward - so every irrigation tool invocation paid a full
    password-grant round trip before pydrawise's own caching ever got a
    chance to help (issue #75). The client is now cached at module level,
    keyed by (username, password), and reused across calls in the same
    process; it's rebuilt if credentials change (e.g. a fresh
    `lawnops configure` mid-process).

    Raises RuntimeError if credentials are missing.
    """
    global _client, _client_credentials

    from lawnops.config import load_config

    config = load_config()
    hydrawise_config = config.get("hydrawise", {})
    username = hydrawise_config.get("username", "")
    password = hydrawise_config.get("password", "")
    if not username or not password:
        raise RuntimeError(
            "Set hydrawise.username and hydrawise.password in "
            "~/.config/lawnops/config.json or HYDRAWISE_USERNAME/HYDRAWISE_PASSWORD env vars"
        )

    credentials = (username, password)
    if _client is None or _client_credentials != credentials:
        auth = HydrawiseAuth(username, password)
        _client = Hydrawise(auth)
        _client_credentials = credentials
    return _client


async def fetch_controller(hw):
    """Fetch the first controller with zones.

    Raises RuntimeError if no controllers found.
    """
    user = await hw.get_user(fetch_zones=True)
    if not user.controllers:
        raise RuntimeError("No controllers found on account")
    return user.controllers[0]


def get_status():
    """Fetch controller status, sensors, programs, and zone details.

    Returns (controller, sensors, programs_dict) where programs_dict maps
    program_id to {name, start, period, method, monthly, zones}.
    """

    async def _fetch():
        hw = get_client()
        ctrl = await fetch_controller(hw)
        sensors = await hw.get_sensors(ctrl)

        programs = {}
        for zone in ctrl.zones:
            ws = zone.watering_settings
            if hasattr(ws, "standard_program_applications"):
                for app in ws.standard_program_applications:
                    prog = app.standard_program
                    if prog.id not in programs:
                        programs[prog.id] = {
                            "name": prog.name,
                            "start": prog.start_times[0].strftime("%H:%M") if prog.start_times else "-",
                            "period": prog.periodicity.period if prog.periodicity else "-",
                            "method": prog.scheduling_method.label if prog.scheduling_method else "-",
                            "monthly": prog.monthly_watering_adjustments,
                            "zones": [],
                        }
                    programs[prog.id]["zones"].append(zone.number.value)

        return ctrl, sensors, programs

    return run_async(_fetch())


def run_zone(zone_num, minutes):
    """Run a specific zone for N minutes.

    Returns (zone_name, zone_num, minutes).
    Raises RuntimeError if zone not found.
    """

    async def _run():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        target = None
        for z in ctrl.zones:
            if z.number.value == zone_num:
                target = z
                break
        if not target:
            raise RuntimeError(f"Zone {zone_num} not found")

        await hw.start_zone(target, custom_run_duration=minutes * 60)
        return target.name, zone_num, minutes

    return run_async(_run())


def run_all(minutes, zones=None):
    """Run all zones (or specific zones) sequentially for N minutes each.

    Returns list of (zone_num, zone_name) that were queued.
    Raises RuntimeError if a specified zone is not found.
    """

    async def _runall():
        from gql.dsl import DSLMutation, dsl_gql
        from pydrawise.schema import DSL_SCHEMA

        hw = get_client()
        ctrl = await fetch_controller(hw)

        if zones:
            targets = []
            for zone_num in zones:
                target = None
                for z in ctrl.zones:
                    if z.number.value == zone_num:
                        target = z
                        break
                if not target:
                    raise RuntimeError(f"Zone {zone_num} not found")
                targets.append(target)

            zone_ids = [t.id for t in targets]
            run_durations = [minutes * 60] * len(targets)

            async with await hw._client() as session:
                mutation_field = DSL_SCHEMA.Mutation.startSelectedZones.args(
                    zoneIds=zone_ids,
                    runDurations=run_durations,
                    markRunAsScheduled=False,
                ).select(DSL_SCHEMA.StatusCodeAndSummary.status)
                await session.execute(dsl_gql(DSLMutation(mutation_field)))

            return [(t.number.value, t.name) for t in targets]
        else:
            await hw.start_all_zones(ctrl, custom_run_duration=minutes * 60)
            return [(z.number.value, z.name) for z in ctrl.zones]

    return run_async(_runall())


def stop(zone_num=None):
    """Stop a specific zone or all zones.

    Returns zone_name if a specific zone was stopped, or None for all.
    Raises RuntimeError if zone not found.
    """

    async def _stop():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        if zone_num:
            target = None
            for z in ctrl.zones:
                if z.number.value == zone_num:
                    target = z
                    break
            if not target:
                raise RuntimeError(f"Zone {zone_num} not found")
            await hw.stop_zone(target)
            return target.name
        else:
            await hw.stop_all_zones(ctrl)
            return None

    return run_async(_stop())


def suspend(hours, zone_num=None):
    """Suspend zone(s) for N hours.

    Returns (until_str, zone_name_or_None).
    Raises RuntimeError if zone not found.
    """
    until = datetime.now() + timedelta(hours=hours)
    until_str = until.strftime("%Y-%m-%d %H:%M")

    async def _suspend():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        if zone_num:
            target = None
            for z in ctrl.zones:
                if z.number.value == zone_num:
                    target = z
                    break
            if not target:
                raise RuntimeError(f"Zone {zone_num} not found")
            await hw.suspend_zone(target, until=until)
            return until_str, target.name
        else:
            await hw.suspend_all_zones(ctrl, until=until)
            return until_str, None

    return run_async(_suspend())


def resume(zone_num=None):
    """Resume suspended zone(s).

    Returns zone_name if a specific zone was resumed, or None for all.
    Raises RuntimeError if zone not found.
    """

    async def _resume():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        if zone_num:
            target = None
            for z in ctrl.zones:
                if z.number.value == zone_num:
                    target = z
                    break
            if not target:
                raise RuntimeError(f"Zone {zone_num} not found")
            await hw.resume_zone(target)
            return target.name
        else:
            await hw.resume_all_zones(ctrl)
            return None

    return run_async(_resume())


async def _fetch_program_meta(session, ctrl_id):
    """Query conditionalWateringAdjustments and schedulingMethod labels for all programs.

    Returns dict: program_id -> {schedule_adjustments, schedule_adjustment_ids, scheduling_method_label}
    schedule_adjustments is a list of {id, label} dicts for all active conditions.
    schedule_adjustment_ids is the flat list of IDs (used by updateStandardProgram mutation).
    """
    from gql import gql

    query = gql("""
        query GetProgramMeta($controllerId: Int!) {
            controller(controllerId: $controllerId) {
                zones {
                    wateringSettings {
                        ... on StandardWateringSettings {
                            standardProgramApplications {
                                standardProgram {
                                    id
                                    schedulingMethod { value label }
                                    conditionalWateringAdjustments(controllerId: $controllerId) { id label }
                                }
                            }
                        }
                    }
                }
            }
        }
    """)
    result = await session.execute(query, variable_values={"controllerId": ctrl_id})
    meta = {}
    for zone in result.get("controller", {}).get("zones", []):
        for app in zone.get("wateringSettings", {}).get("standardProgramApplications", []):
            prog = app.get("standardProgram", {})
            pid = prog.get("id")
            if pid and pid not in meta:
                adjustments = prog.get("conditionalWateringAdjustments", [])
                meta[pid] = {
                    "schedule_adjustments": [{"id": a["id"], "label": a["label"]} for a in adjustments],
                    "schedule_adjustment_ids": [a["id"] for a in adjustments],
                    "scheduling_method_label": prog.get("schedulingMethod", {}).get("label", "Time Based"),
                }
    return meta


def get_programs():
    """Fetch all standard programs with full detail for display and update.

    Returns list of dicts, each with keys:
      id, name, program_type, scheduling_method_label, schedule_adjustment_ids,
      day_pattern, start_times, period_days, series_start, ignore_rain_sensor,
      monthly_adjustments,
      zones: [{zone_id, zone_num, zone_name, run_duration_min, run_time_group_id}]
    """

    async def _fetch():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        programs = {}
        for zone in ctrl.zones:
            ws = zone.watering_settings
            if not hasattr(ws, "standard_program_applications"):
                continue
            for app in ws.standard_program_applications:
                prog = app.standard_program
                if prog.id not in programs:
                    start_times = [t.strftime("%H:%M") for t in prog.start_times] if prog.start_times else []
                    programs[prog.id] = {
                        "id": prog.id,
                        "name": prog.name,
                        "program_type": prog.scheduling_method.value if prog.scheduling_method else 1,
                        "scheduling_method_label": prog.scheduling_method.label
                        if prog.scheduling_method
                        else "Time Based",
                        "schedule_adjustments": [],
                        "schedule_adjustment_ids": [],
                        "day_pattern": prog.standard_program_day_pattern or "interval",
                        "start_times": start_times,
                        "period_days": prog.periodicity.period if prog.periodicity else None,
                        "series_start": int(prog.periodicity.series_start.timestamp())
                        if prog.periodicity and prog.periodicity.series_start
                        else None,
                        "ignore_rain_sensor": prog.ignore_rain_sensor,
                        "monthly_adjustments": prog.monthly_watering_adjustments,
                        "zones": [],
                    }
                duration_min = (
                    int(app.run_time_group.duration.total_seconds() / 60) if app.run_time_group.duration else 0
                )
                programs[prog.id]["zones"].append(
                    {
                        "zone_id": zone.id,
                        "zone_num": zone.number.value,
                        "zone_name": zone.name,
                        "run_duration_min": duration_min,
                        "run_time_group_id": app.run_time_group.id,
                    }
                )

        # Sort zones within each program by zone number
        for prog in programs.values():
            prog["zones"].sort(key=lambda z: z["zone_num"])

        # Enrich with conditionalWateringAdjustments and confirmed scheduling method label
        async with await hw._client() as session:
            meta = await _fetch_program_meta(session, ctrl.id)
        for pid, m in meta.items():
            if pid in programs:
                programs[pid]["schedule_adjustments"] = m["schedule_adjustments"]
                programs[pid]["schedule_adjustment_ids"] = m["schedule_adjustment_ids"]
                programs[pid]["scheduling_method_label"] = m["scheduling_method_label"]

        return ctrl, list(programs.values())

    return run_async(_fetch())


def update_program(
    program_id,
    add_zone_nums=None,
    remove_zone_nums=None,
    period_days=None,
    start_time=None,
    run_duration_min=None,
    seasonal_adjustment_factors=None,
    schedule_adjustment_ids=None,
):
    """Update a standard program: add/remove zones, change frequency or start time.

    Args:
        program_id: The program ID to update (from get_programs()).
        add_zone_nums: List of zone numbers to add to the program.
        remove_zone_nums: List of zone numbers to remove from the program.
        period_days: New watering interval in days (for interval-type programs).
        start_time: New start time string "HH:MM" (replaces existing start times).
        run_duration_min: New run duration in minutes applied to all zones in the program.
        schedule_adjustment_ids: Explicit list of predictive watering condition IDs to set.
            When provided, replaces the current conditions. When None, current conditions
            are preserved. Pass [] to clear all conditions.

    Returns dict with updated program summary.
    Raises RuntimeError if program not found or update fails.
    """

    async def _update():
        from pydrawise.schema import DSL_SCHEMA

        hw = get_client()
        ctrl = await fetch_controller(hw)

        # Build zone map: zone_num -> zone object
        zone_map = {z.number.value: z for z in ctrl.zones}

        # Find the target program by scanning zone watering settings
        target_prog = None
        all_apps = []  # list of (zone, app) for the target program

        for zone in ctrl.zones:
            ws = zone.watering_settings
            if not hasattr(ws, "standard_program_applications"):
                continue
            for app in ws.standard_program_applications:
                if app.standard_program.id == program_id:
                    target_prog = app.standard_program
                    all_apps.append((zone, app))

        if target_prog is None:
            raise RuntimeError(f"Program ID {program_id} not found")

        # Build current zone run times dict: zone_num -> {zone_id, duration_sec, run_time_group_id}
        current_zone_data = {}
        for zone, app in all_apps:
            znum = zone.number.value
            current_zone_data[znum] = {
                "zone_num": znum,
                "zone_id": zone.id,
                "duration_sec": int(app.run_time_group.duration.total_seconds()),
                "run_time_group_id": app.run_time_group.id,
            }

        # Apply add/remove zone changes
        if remove_zone_nums:
            for znum in remove_zone_nums:
                if znum not in current_zone_data:
                    raise RuntimeError(f"Zone {znum} is not in program '{target_prog.name}'")
                del current_zone_data[znum]

        if add_zone_nums:
            for znum in add_zone_nums:
                if znum not in zone_map:
                    raise RuntimeError(f"Zone {znum} not found on controller")
                if znum not in current_zone_data:
                    # New zone - use run_duration_min if given, else default 10 min
                    default_dur = (run_duration_min * 60) if run_duration_min else 600
                    current_zone_data[znum] = {
                        "zone_num": znum,
                        "zone_id": zone_map[znum].id,
                        "duration_sec": default_dur,
                        "run_time_group_id": None,
                    }

        # Apply duration change to all zones if requested
        if run_duration_min is not None:
            for zdata in current_zone_data.values():
                zdata["duration_sec"] = run_duration_min * 60

        # Build zoneRunTimes list for the mutation
        zone_run_times = []
        for zdata in sorted(current_zone_data.values(), key=lambda z: z["zone_num"]):
            entry = {
                "zoneNumber": zdata["zone_num"],
                "runDuration": int(zdata["duration_sec"] / 60),  # API expects minutes
            }
            if zdata["run_time_group_id"]:
                entry["runTimeGroupId"] = zdata["run_time_group_id"]
            zone_run_times.append(entry)

        if not zone_run_times:
            raise RuntimeError("Cannot remove all zones from a program")

        # Resolve start times
        if start_time:
            new_start_times = [start_time]
        else:
            new_start_times = (
                [t.strftime("%H:%M") for t in target_prog.start_times] if target_prog.start_times else ["06:00"]
            )

        # Resolve period
        new_period = (
            period_days
            if period_days is not None
            else (target_prog.periodicity.period if target_prog.periodicity else 1)
        )
        if target_prog.periodicity and target_prog.periodicity.series_start:
            new_series_start = int(target_prog.periodicity.series_start.timestamp())
        else:
            # No series_start set (e.g. after program type change) - anchor to today midnight
            from datetime import date

            today_midnight = datetime.combine(date.today(), datetime.min.time())
            new_series_start = int(today_midnight.timestamp())

        # Use provided IDs or fetch current ones to preserve predictive watering conditions
        if schedule_adjustment_ids is not None:
            current_adjustment_ids = schedule_adjustment_ids
        else:
            async with await hw._client() as session:
                meta = await _fetch_program_meta(session, ctrl.id)
            current_adjustment_ids = meta.get(program_id, {}).get("schedule_adjustment_ids", [])

        # Call updateStandardProgram mutation directly via DSL_SCHEMA
        mutation_args = {
            "programId": program_id,
            "controllerId": ctrl.id,
            "name": target_prog.name,
            "programType": target_prog.scheduling_method.value if target_prog.scheduling_method else 1,
            "dayPattern": target_prog.standard_program_day_pattern or "interval",
            "startTimes": new_start_times,
            "zoneRunTimes": zone_run_times,
            "scheduleAdjustmentIds": current_adjustment_ids,
            "seasonalAdjustmentFactors": seasonal_adjustment_factors
            if seasonal_adjustment_factors is not None
            else (target_prog.monthly_watering_adjustments or []),
        }

        if target_prog.standard_program_day_pattern:
            mutation_args["standardProgramDayPattern"] = target_prog.standard_program_day_pattern
        if new_period is not None:
            mutation_args["interval"] = new_period
        mutation_args["seriesStart"] = new_series_start
        mutation_args["ignoreRainSensor"] = target_prog.ignore_rain_sensor

        # Execute the mutation
        async with await hw._client() as session:
            from gql.dsl import DSLMutation, dsl_gql

            mutation_field = DSL_SCHEMA.Mutation.updateStandardProgram.args(**mutation_args).select(
                DSL_SCHEMA.StandardProgram.id,
                DSL_SCHEMA.StandardProgram.name,
            )
            result = await session.execute(dsl_gql(DSLMutation(mutation_field)))

        updated = result.get("updateStandardProgram", {})
        applied_factors = (
            seasonal_adjustment_factors
            if seasonal_adjustment_factors is not None
            else (target_prog.monthly_watering_adjustments or [])
        )
        return {
            "program_id": updated.get("id", program_id),
            "name": updated.get("name", target_prog.name),
            "zones_in_program": sorted(current_zone_data.keys()),
            "start_times": new_start_times,
            "period_days": new_period,
            "seasonal_adjustment_factors": applied_factors,
        }

    return run_async(_update())


def update_zone_settings(zone_num, fixed_watering_adjustment):
    """Update per-zone ET/Smart Watering adjustment percentage.

    Args:
        zone_num: Zone number (1-8).
        fixed_watering_adjustment: Adjustment percentage (100 = 100% of calculated
            watering, 110 = 10% more water, 80 = 20% less). Range: 0-200.

    Returns dict with zone_num, zone_name, old_adjustment, new_adjustment.
    Raises RuntimeError if zone not found or update fails.
    """

    async def _update():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        target = None
        for z in ctrl.zones:
            if z.number.value == zone_num:
                target = z
                break
        if not target:
            raise RuntimeError(f"Zone {zone_num} not found")

        old_adjustment = target.watering_settings.fixed_watering_adjustment

        async with await hw._client() as session:
            from gql.dsl import DSLMutation, dsl_gql
            from pydrawise.schema import DSL_SCHEMA

            mutation_field = DSL_SCHEMA.Mutation.updateZoneStandard.args(
                zoneId=target.id,
                name=target.name,
                number=target.number.value,
                globalMasterValve=-1,
                wateringAdjustment=fixed_watering_adjustment,
                cycleSoakEnable=False,
            ).select(
                DSL_SCHEMA.Zone.id,
                DSL_SCHEMA.Zone.name,
            )
            result = await session.execute(dsl_gql(DSLMutation(mutation_field)))

        updated = result.get("updateZoneStandard", {})
        return {
            "zone_num": zone_num,
            "zone_name": updated.get("name", target.name),
            "old_adjustment": old_adjustment,
            "new_adjustment": fixed_watering_adjustment,
        }

    return run_async(_update())


def execute_apply(plan: dict) -> dict:
    """Execute an apply plan produced by irrigation_config.plan_apply().

    Calls update_program() for each program in plan["updates"].  Does NOT
    touch run/stop/suspend/resume or alter any zone suspension or in-progress
    run state.

    Args:
        plan: The dict returned by plan_apply().  Must contain "updates" and
            "skipped" keys.

    Returns a structured report dict::

        {
          "applied": [
            {
              "program_id": int,
              "program_name": str,
              "fields_applied": [str],
              "result": dict,       # update_program() return value
            },
            ...
          ],
          "skipped": [             # pass-through from plan["skipped"]
            {
              "path": str,
              "category": str,
              "reason": str,
            },
            ...
          ],
          "errors": [
            {
              "program_id": int,
              "program_name": str,
              "error": str,
            },
            ...
          ],
        }

    Raises nothing -- errors per program are captured in the "errors" list.
    The caller inspects "errors" and decides whether to surface them as
    a failure.
    """
    applied: list[dict] = []
    errors: list[dict] = []

    for update in plan.get("updates", []):
        prog_id = update["program_id"]
        prog_name = update["program_name"]
        kwargs = update.get("kwargs", {})
        fields_applied = update.get("fields_applied", [])

        try:
            result = update_program(prog_id, **kwargs)
            applied.append(
                {
                    "program_id": prog_id,
                    "program_name": prog_name,
                    "fields_applied": fields_applied,
                    "result": result,
                }
            )
        except Exception as e:
            errors.append(
                {
                    "program_id": prog_id,
                    "program_name": prog_name,
                    "error": str(e),
                }
            )

    return {
        "applied": applied,
        "skipped": plan.get("skipped", []),
        "errors": errors,
    }


def get_history(days=7):
    """Get recent watering history from Hydrawise.

    Returns list of dicts with keys: zone_name, zone_num, run_time,
    duration, status, and raw event data for logging.
    """

    async def _history():
        hw = get_client()
        ctrl = await fetch_controller(hw)

        end = datetime.now()
        start = end - timedelta(days=days)
        report = await hw.get_watering_report(ctrl, start=start, end=end)

        results = []
        for entry in report:
            ev = entry.run_event
            zone_name = ev.zone.name if hasattr(ev, "zone") else "Unknown"
            zone_num = ev.zone.number.value if hasattr(ev, "zone") and hasattr(ev.zone, "number") else 0
            run_time = ev.reported_start_time.strftime("%Y-%m-%d %H:%M") if hasattr(ev, "reported_start_time") else "?"
            duration = ev.reported_duration if hasattr(ev, "reported_duration") else "?"
            status = ev.reported_status if hasattr(ev, "reported_status") else "?"

            result = {
                "zone_name": zone_name,
                "zone_num": zone_num,
                "run_time": run_time,
                "duration": duration,
                "status": status,
            }
            # Include raw data for DB logging
            if hasattr(ev, "reported_start_time"):
                result["log_date"] = ev.reported_start_time.strftime("%Y-%m-%d")
                result["log_start_time"] = ev.reported_start_time.strftime("%H:%M")
                result["log_duration_min"] = (
                    ev.reported_duration.total_seconds() / 60
                    if hasattr(ev, "reported_duration") and ev.reported_duration
                    else None
                )
            results.append(result)

        return results

    return run_async(_history())
