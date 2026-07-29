"""Declarative desired-state schema and serialization for Hydrawise controller config.

This module defines the YAML schema for irrigation desired-state and provides
helpers to serialize live pydrawise objects into that schema.

ADVISORY NOTE ON ZONE-LEVEL SETTINGS
--------------------------------------
Zone-level writes (watering_adjustment_pct, cycle_soak) are blocked by a
Hydrawise server-side internal error on all updateZone* mutations as of
2026-06-21.  These fields are captured here for export and diff visibility
but are explicitly marked advisory_only=true in the output.  They will
never be submitted by irrigation_apply until Hydrawise fixes the mutation.

Usage::

    from lawnops.irrigation_config import export_config, write_yaml, read_yaml

    ctrl, programs = get_programs()
    cfg = export_config(ctrl, programs)
    write_yaml(cfg)  # defaults to ~/.config/lawnops/irrigation_state.yaml

    # Round-trip
    loaded = read_yaml()

    # Plan (dry-run) or execute apply
    from lawnops.irrigation_config import plan_apply
    plan = plan_apply(diff_result, desired_cfg)
    # plan["updates"] -> list of per-program update_program kwargs dicts
    # plan["skipped"] -> list of {path, reason, category}
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from lawnops.atomic_io import atomic_write
from lawnops.config import CONFIG_DIR

# ---------------------------------------------------------------------------
# Schema version -- bump whenever the YAML structure changes in a breaking way
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "1"

# Default snapshot path: the user's local lawnops config dir (~/.config/lawnops).
# Deliberately NOT inside the source repo -- exported config is operational home
# state, not code, and must never be committed to a repo that could go public.
# Covered by local backups (e.g. Arq); point --output elsewhere for a custom path.
DEFAULT_SNAPSHOT_PATH = CONFIG_DIR / "irrigation_state.yaml"


# ---------------------------------------------------------------------------
# Serialize helpers
# ---------------------------------------------------------------------------


def _serialize_zone(zone_obj: Any, run_duration_min: int | None = None) -> dict:
    """Serialize a pydrawise Zone object to a schema dict.

    run_duration_min is supplied from the program application context (not
    available on the zone object itself).

    Fields marked advisory_only are read/diff-able but never written via apply.
    """
    ws = zone_obj.watering_settings

    # Cycle/soak -- may be None when not configured on controller
    cycle_soak = None
    css = getattr(ws, "cycle_and_soak_settings", None)
    if css is not None:
        cycle_dur = getattr(css, "cycle_duration", None)
        soak_dur = getattr(css, "soak_duration", None)
        cycle_soak = {
            "advisory_only": True,
            "ise_blocked": True,
            "cycle_min": int(cycle_dur.total_seconds() / 60) if cycle_dur is not None else None,
            "soak_min": int(soak_dur.total_seconds() / 60) if soak_dur is not None else None,
        }

    result: dict = {
        "zone_num": zone_obj.number.value,
        "zone_name": zone_obj.name,
    }
    if run_duration_min is not None:
        result["run_duration_min"] = run_duration_min

    # Per-zone watering adjustment -- advisory only (ISE-blocked writes)
    result["watering_adjustment_pct"] = {
        "advisory_only": True,
        "ise_blocked": True,
        "value": getattr(ws, "fixed_watering_adjustment", None),
    }

    if cycle_soak is not None:
        result["cycle_soak"] = cycle_soak

    return result


def _serialize_program(prog_dict: dict) -> dict:
    """Convert a get_programs() program dict to the desired-state schema.

    prog_dict shape (from irrigation.get_programs()):
      id, name, program_type, scheduling_method_label, schedule_adjustments,
      schedule_adjustment_ids, day_pattern, start_times, period_days,
      series_start, ignore_rain_sensor, monthly_adjustments,
      zones: [{zone_id, zone_num, zone_name, run_duration_min, run_time_group_id}]
    """
    monthly = prog_dict.get("monthly_adjustments") or []
    # Normalize to exactly 12 elements (pad with 0 if shorter, which shouldn't happen)
    while len(monthly) < 12:
        monthly.append(0)
    monthly = monthly[:12]

    zones_out = []
    for z in prog_dict.get("zones", []):
        zone_entry = {
            "zone_num": z["zone_num"],
            "zone_name": z["zone_name"],
            "run_duration_min": z["run_duration_min"],
        }
        zones_out.append(zone_entry)

    return {
        "id": prog_dict["id"],
        "name": prog_dict["name"],
        "program_type": prog_dict.get("scheduling_method_label", "Time Based"),
        "day_pattern": prog_dict.get("day_pattern", "interval"),
        "period_days": prog_dict.get("period_days"),
        "start_times": prog_dict.get("start_times", []),
        "ignore_rain_sensor": prog_dict.get("ignore_rain_sensor", False),
        "predictive_watering_ids": prog_dict.get("schedule_adjustment_ids", []),
        "predictive_watering_labels": [a["label"] for a in prog_dict.get("schedule_adjustments", [])],
        "seasonal_adjustment_factors": monthly,
        "zones": zones_out,
    }


def export_config(ctrl: Any, programs: list[dict]) -> dict:
    """Build the full desired-state dict from a live pydrawise controller + programs.

    ctrl is the pydrawise Controller object (from fetch_controller).
    programs is the list returned by get_programs() (second element of the tuple).

    The returned dict is the authoritative in-memory representation of the
    desired-state schema.  Write it to YAML with write_yaml().
    """
    exported_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Build a zone_num -> pydrawise Zone mapping for per-zone settings
    zone_obj_map: dict[int, Any] = {z.number.value: z for z in ctrl.zones}

    # Build per-program zone rows (zone_num -> run_duration_min) from programs
    # so we can attach run_duration_min to the zone_obj serialization
    # last-program-wins when a zone appears in multiple programs; acceptable for
    # the catalog snapshot, but Phase 3 apply must handle per-program durations
    # separately rather than relying on this flattened map.
    zone_duration_map: dict[int, int] = {}
    for prog in programs:
        for z in prog.get("zones", []):
            zone_duration_map[z["zone_num"]] = z["run_duration_min"]

    # Serialize all zones with advisory fields (complete zone catalog)
    zones_catalog = []
    for znum in sorted(zone_obj_map.keys()):
        zobj = zone_obj_map[znum]
        rdm = zone_duration_map.get(znum)
        zones_catalog.append(_serialize_zone(zobj, run_duration_min=rdm))

    programs_out = [_serialize_program(p) for p in programs]

    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": exported_at,
        "controller": {
            "name": ctrl.name,
            "online": ctrl.online,
            "firmware": ctrl.software_version,
        },
        "programs": programs_out,
        "zones_catalog": zones_catalog,
    }


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


def write_yaml(config_dict: dict, path: Path | str | None = None) -> Path:
    """Write desired-state dict to YAML file.

    If path is None, uses DEFAULT_SNAPSHOT_PATH.
    Creates parent directories as needed.
    Returns the resolved Path that was written.
    """
    target = Path(path) if path is not None else DEFAULT_SNAPSHOT_PATH
    target = target.expanduser().resolve()
    atomic_write(
        target,
        lambda f: yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True),
    )
    return target


def read_yaml(path: Path | str | None = None) -> dict:
    """Read desired-state YAML file and return as dict.

    If path is None, uses DEFAULT_SNAPSHOT_PATH.
    Raises FileNotFoundError if the file does not exist.
    """
    target = Path(path) if path is not None else DEFAULT_SNAPSHOT_PATH
    target = target.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Irrigation config not found: {target}")
    with open(target) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

# Volatile / live-only fields are excluded from the diff by structural omission:
# diff_config only visits controller, programs, and zones_catalog, so top-level
# `exported_at` and `schema_version` are never compared. Controller comparison
# (see _diff_controller) explicitly allowlists name/firmware, so the live-only
# `online` and `last_contact` are likewise never compared.

# Program-level fields that can be written via updateStandardProgram in Phase 3.
# All other program fields that appear in the schema but aren't in this set are
# still diffed but would need a new mutation path in Phase 3.
# Note: predictive_watering_labels is intentionally excluded -- it is a derived
# echo of predictive_watering_ids, so diffing it would double-report one logical
# change (and Phase 3 apply writes the IDs, not the labels).
_PROGRAM_LEVEL_FIELDS = frozenset(
    {
        "name",
        "program_type",
        "day_pattern",
        "period_days",
        "start_times",
        "ignore_rain_sensor",
        "predictive_watering_ids",
        "seasonal_adjustment_factors",
        # zones list changes (add/remove/reorder) are program-level mutations
        "zones",
    }
)

# Zone catalog fields that are advisory / ISE-blocked. Cannot be submitted
# until Hydrawise fixes their updateZone* mutations.
_ZONE_ADVISORY_FIELDS = frozenset({"watering_adjustment_pct", "cycle_soak"})


def _changes_for_value(path: str, live_val: Any, desired_val: Any, category: str) -> list[dict]:
    """Return a list of change entries if live_val != desired_val, else empty list."""
    if live_val == desired_val:
        return []
    return [
        {
            "path": path,
            "live": live_val,
            "desired": desired_val,
            "category": category,
        }
    ]


def _diff_programs(live_programs: list[dict], desired_programs: list[dict]) -> list[dict]:
    """Compare program lists; return flat list of change entries."""
    changes: list[dict] = []

    live_by_id: dict[int, dict] = {p["id"]: p for p in live_programs}
    desired_by_id: dict[int, dict] = {p["id"]: p for p in desired_programs}

    # Removed programs (in live, not in desired)
    for prog_id, prog in live_by_id.items():
        if prog_id not in desired_by_id:
            changes.append(
                {
                    "path": f"programs[id={prog_id}]",
                    "live": prog.get("name"),
                    "desired": None,
                    "category": "program_level",
                    "action": "removed",
                }
            )

    # Added programs (in desired, not in live)
    for prog_id, prog in desired_by_id.items():
        if prog_id not in live_by_id:
            changes.append(
                {
                    "path": f"programs[id={prog_id}]",
                    "live": None,
                    "desired": prog.get("name"),
                    "category": "program_level",
                    "action": "added",
                }
            )

    # Changed programs (in both)
    for prog_id in sorted(set(live_by_id) & set(desired_by_id)):
        live_prog = live_by_id[prog_id]
        desired_prog = desired_by_id[prog_id]
        prog_label = f"programs[id={prog_id}]"

        for field in _PROGRAM_LEVEL_FIELDS:
            lv = live_prog.get(field)
            dv = desired_prog.get(field)
            changes.extend(_changes_for_value(f"{prog_label}.{field}", lv, dv, "program_level"))

    return changes


def _diff_zones_catalog(live_zones: list[dict], desired_zones: list[dict]) -> list[dict]:
    """Compare zones_catalog lists; return flat list of change entries.

    Zone-level advisory fields (watering_adjustment_pct, cycle_soak) are
    categorized as zone_level -- they are diffed for visibility but cannot
    be submitted until Hydrawise fixes their updateZone* mutations.
    Non-advisory fields (zone_name, run_duration_min) are program_level
    because run durations are managed through program zone memberships.
    """
    changes: list[dict] = []

    live_by_num: dict[int, dict] = {z["zone_num"]: z for z in live_zones}
    desired_by_num: dict[int, dict] = {z["zone_num"]: z for z in desired_zones}

    # Removed zones
    for znum in sorted(set(live_by_num) - set(desired_by_num)):
        changes.append(
            {
                "path": f"zones_catalog[zone_num={znum}]",
                "live": live_by_num[znum].get("zone_name"),
                "desired": None,
                "category": "program_level",
                "action": "removed",
            }
        )

    # Added zones
    for znum in sorted(set(desired_by_num) - set(live_by_num)):
        changes.append(
            {
                "path": f"zones_catalog[zone_num={znum}]",
                "live": None,
                "desired": desired_by_num[znum].get("zone_name"),
                "category": "program_level",
                "action": "added",
            }
        )

    for znum in sorted(set(live_by_num) & set(desired_by_num)):
        lz = live_by_num[znum]
        dz = desired_by_num[znum]
        zone_label = f"zones_catalog[zone_num={znum}]"

        for field in ("zone_name", "run_duration_min"):
            changes.extend(_changes_for_value(f"{zone_label}.{field}", lz.get(field), dz.get(field), "program_level"))

        for field in _ZONE_ADVISORY_FIELDS:
            lv = lz.get(field)
            dv = dz.get(field)
            if lv != dv:
                changes.append(
                    {
                        "path": f"{zone_label}.{field}",
                        "live": lv,
                        "desired": dv,
                        "category": "zone_level",
                        "advisory": True,
                        "ise_blocked": True,
                        "note": "Zone-level writes blocked by Hydrawise server-side ISE. Not actionable until Hydrawise fixes updateZone* mutations.",
                    }
                )

    return changes


def _diff_controller(live_ctrl: dict, desired_ctrl: dict) -> list[dict]:
    """Compare controller metadata. The (name, firmware) tuple is an explicit
    allowlist; volatile live-only fields (online, last_contact) are excluded by
    not appearing here."""
    changes: list[dict] = []
    for field in ("name", "firmware"):
        changes.extend(
            _changes_for_value(f"controller.{field}", live_ctrl.get(field), desired_ctrl.get(field), "program_level")
        )
    return changes


def diff_config(live: dict, desired: dict) -> dict:
    """Compare a live config dict against a desired-state dict.

    Both dicts must follow the irrigation_config schema (produced by export_config
    or read_yaml).  Volatile metadata fields (exported_at, controller.online,
    controller.last_contact) are ignored so they never show as drift.

    Returns a structured result dict::

        {
          "has_drift": bool,
          "program_level_count": int,   # actionable changes (writable in Phase 3)
          "zone_level_count": int,      # advisory / ISE-blocked changes
          "total_count": int,
          "changes": [
            {
              "path": str,              # dotted path within schema
              "live": <value>,          # current live value
              "desired": <value>,       # value in the desired-state file
              "category": "program_level" | "zone_level",
              # zone_level entries also carry:
              "advisory": true,
              "ise_blocked": true,
              "note": str,
            },
            ...
          ]
        }
    """
    changes: list[dict] = []

    # Controller metadata (name, firmware -- not online/last_contact)
    live_ctrl = live.get("controller", {})
    desired_ctrl = desired.get("controller", {})
    changes.extend(_diff_controller(live_ctrl, desired_ctrl))

    # Programs
    live_programs = live.get("programs", [])
    desired_programs = desired.get("programs", [])
    changes.extend(_diff_programs(live_programs, desired_programs))

    # Zones catalog
    live_zones = live.get("zones_catalog", [])
    desired_zones = desired.get("zones_catalog", [])
    changes.extend(_diff_zones_catalog(live_zones, desired_zones))

    program_level_count = sum(1 for c in changes if c.get("category") == "program_level")
    zone_level_count = sum(1 for c in changes if c.get("category") == "zone_level")

    return {
        "has_drift": len(changes) > 0,
        "program_level_count": program_level_count,
        "zone_level_count": zone_level_count,
        "total_count": len(changes),
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Apply plan logic (Phase 3)
# ---------------------------------------------------------------------------

# Fields reported as drift but not writable via the current update_program
# signature.  They appear in _PROGRAM_LEVEL_FIELDS for diff visibility but
# require an extended mutation (or manual controller action) to change.
_NON_WRITABLE_PROGRAM_FIELDS = frozenset({"name", "program_type", "day_pattern", "ignore_rain_sensor"})

# Fields writable via update_program that map to a kwarg by the same name.
_WRITABLE_DIRECT = frozenset({"period_days", "seasonal_adjustment_factors"})


def plan_apply(diff_result: dict, desired: dict) -> dict:
    """Translate a diff_config result into a structured apply plan.

    This is PURE LOGIC -- no network calls, no side effects.  Pass the result
    to execute_apply() (in irrigation.py) to perform the actual writes.

    Writable via update_program (current signature):
      - seasonal_adjustment_factors
      - period_days
      - start_times  (passed as start_time=first_element to update_program)
      - predictive_watering_ids  (passed as schedule_adjustment_ids)
      - zones list changes  (zone add/remove and run_duration_min adjustments)

    Not writable via current update_program signature (skipped with reason):
      - name, program_type, day_pattern, ignore_rain_sensor
        -> "not writable via updateStandardProgram (needs extended mutation)"

    Zone-level changes (watering_adjustment_pct, cycle_soak) are always
    skipped (ISE-blocked) -- update_program is never called for them.

    Controller-level changes (name, firmware) are skipped -- no write path.

    Args:
        diff_result: The dict returned by diff_config().
        desired: The full desired-state dict (from read_yaml()), used to
            extract per-program desired values for writable fields.

    Returns a plan dict::

        {
          "updates": [
            {
              "program_id": int,
              "program_name": str,   # from desired
              "kwargs": {            # kwargs to pass to update_program()
                "period_days": int | None,
                "start_time": str | None,
                "seasonal_adjustment_factors": list | None,
                "schedule_adjustment_ids": list | None,
                "add_zone_nums": list | None,
                "remove_zone_nums": list | None,
                "run_duration_min": int | None,
              },
              "fields_applied": [str],   # field names being written
            },
            ...
          ],
          "skipped": [
            {
              "path": str,
              "category": str,       # "program_level" or "zone_level"
              "reason": str,
            },
            ...
          ],
        }
    """
    desired_programs_by_id: dict[int, dict] = {p["id"]: p for p in desired.get("programs", [])}

    # Collect program-level changes grouped by program_id
    # path pattern: "programs[id=<N>].<field>" or "programs[id=<N>]"
    prog_changes: dict[int, list[dict]] = {}
    skipped: list[dict] = []

    for change in diff_result.get("changes", []):
        path = change["path"]
        category = change["category"]

        # Zone-level changes (advisory / ISE-blocked) - always skip
        if category == "zone_level":
            skipped.append(
                {
                    "path": path,
                    "category": "zone_level",
                    "reason": "zone-level write blocked by Hydrawise server-side ISE (updateZone* mutations)",
                }
            )
            continue

        # Controller-level changes - no write path
        if path.startswith("controller."):
            skipped.append(
                {
                    "path": path,
                    "category": "program_level",
                    "reason": "controller metadata changes require manual update on Hydrawise portal",
                }
            )
            continue

        # program removed/added whole-program entries - no write path for add/remove
        if "." not in path or path.endswith("]"):
            action = change.get("action", "changed")
            if action in ("added", "removed"):
                skipped.append(
                    {
                        "path": path,
                        "category": "program_level",
                        "reason": f"program {action} is not supported via updateStandardProgram; manage programs manually on the Hydrawise portal",
                    }
                )
                continue

        # Extract program id from path like "programs[id=101].field"
        prog_id = _extract_program_id(path)
        if prog_id is None:
            # zones_catalog.run_duration_min or zone_name changes go through
            # program update (run durations are per-program), but we can't
            # determine which program owns this zone from the catalog path alone.
            # Report as skipped with a helpful message.
            skipped.append(
                {
                    "path": path,
                    "category": "program_level",
                    "reason": "zones_catalog field change: run_duration_min and zone_name changes are applied via program zone membership -- edit the program's zones list in the desired YAML instead",
                }
            )
            continue

        # Determine the field name from the path
        field = _extract_field(path)

        # Non-writable program fields
        if field in _NON_WRITABLE_PROGRAM_FIELDS:
            skipped.append(
                {
                    "path": path,
                    "category": "program_level",
                    "reason": "not writable via updateStandardProgram (needs extended mutation)",
                }
            )
            continue

        # Accumulate writable changes per program
        if prog_id not in prog_changes:
            prog_changes[prog_id] = []
        prog_changes[prog_id].append(change)

    # Build update kwargs for each program that has writable changes
    updates: list[dict] = []
    for prog_id, changes in sorted(prog_changes.items()):
        desired_prog = desired_programs_by_id.get(prog_id)
        if desired_prog is None:
            # Program was removed in desired -- already handled above as skipped
            continue

        kwargs: dict[str, Any] = {}
        fields_applied: list[str] = []

        for change in changes:
            field = _extract_field(change["path"])

            if field == "seasonal_adjustment_factors":
                kwargs["seasonal_adjustment_factors"] = desired_prog["seasonal_adjustment_factors"]
                fields_applied.append("seasonal_adjustment_factors")

            elif field == "period_days":
                kwargs["period_days"] = desired_prog["period_days"]
                fields_applied.append("period_days")

            elif field == "start_times":
                desired_times = desired_prog.get("start_times") or []
                kwargs["start_time"] = desired_times[0] if desired_times else None
                fields_applied.append("start_times")

            elif field == "predictive_watering_ids":
                kwargs["schedule_adjustment_ids"] = desired_prog.get("predictive_watering_ids", [])
                fields_applied.append("predictive_watering_ids")

            elif field == "zones":
                # Compute add/remove zone sets and run_duration_min
                _apply_zones_kwargs(kwargs, fields_applied, change)

        if fields_applied:
            updates.append(
                {
                    "program_id": prog_id,
                    "program_name": desired_prog.get("name", f"program-{prog_id}"),
                    "kwargs": kwargs,
                    "fields_applied": sorted(set(fields_applied)),
                }
            )

    return {"updates": updates, "skipped": skipped}


def _extract_program_id(path: str) -> int | None:
    """Extract numeric program id from a path like 'programs[id=101].field'."""
    import re

    m = re.search(r"programs\[id=(\d+)\]", path)
    if m:
        return int(m.group(1))
    return None


def _extract_field(path: str) -> str:
    """Extract the final field name from a dotted path.

    Examples::
        "programs[id=101].period_days" -> "period_days"
        "programs[id=101].zones"       -> "zones"
    """
    return path.rsplit(".", 1)[-1]


def _apply_zones_kwargs(kwargs: dict, fields_applied: list[str], change: dict) -> None:
    """Compute add_zone_nums, remove_zone_nums, and run_duration_min from a zones-field change.

    The diff entry for a zones-field change carries:
      change["live"]    -> list of zone dicts from live config
      change["desired"] -> list of zone dicts from desired config

    Each zone dict has {"zone_num": int, "zone_name": str, "run_duration_min": int}.
    """
    live_zones: list[dict] = change.get("live") or []
    desired_zones: list[dict] = change.get("desired") or []

    live_nums = {z["zone_num"] for z in live_zones}
    desired_nums = {z["zone_num"] for z in desired_zones}

    add_nums = sorted(desired_nums - live_nums)
    remove_nums = sorted(live_nums - desired_nums)

    if add_nums:
        kwargs["add_zone_nums"] = add_nums
        fields_applied.append("zones.add")
    if remove_nums:
        kwargs["remove_zone_nums"] = remove_nums
        fields_applied.append("zones.remove")

    # Detect run_duration_min changes for zones present in both
    desired_by_num = {z["zone_num"]: z for z in desired_zones}
    live_by_num = {z["zone_num"]: z for z in live_zones}
    common = live_nums & desired_nums
    duration_changes = {
        znum
        for znum in common
        if desired_by_num[znum].get("run_duration_min") != live_by_num[znum].get("run_duration_min")
    }
    if duration_changes:
        # update_program applies run_duration_min uniformly to all zones.
        # If zones have different desired durations we can only apply the first
        # one detected (per-zone different durations require separate program calls
        # which the current update_program signature does not support).
        # Use the desired duration of the first changed zone (sorted by zone_num).
        first_changed = sorted(duration_changes)[0]
        kwargs["run_duration_min"] = desired_by_num[first_changed]["run_duration_min"]
        fields_applied.append("run_duration_min")
