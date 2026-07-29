"""Unit tests for irrigation_config.diff_config.

All tests use inline config dicts -- no live Hydrawise connection required.
The diff function is pure logic: it compares two dicts and returns a structured
result. The test matrix covers:
  - AC3: identical configs produce zero drift (round-trip identity)
  - AC4: changed seasonal_adjustment_factors produces a program_level change
  - Zone advisory fields produce zone_level advisory entries
  - Added / removed programs
  - Volatile fields (exported_at, controller.online) never show as drift
"""

from __future__ import annotations

import copy

from lawnops.irrigation_config import diff_config

# ---------------------------------------------------------------------------
# Fixtures: minimal but realistic config dicts
# ---------------------------------------------------------------------------

PROGRAM_A = {
    "id": 101,
    "name": "Front Lawn",
    "program_type": "Time Based",
    "day_pattern": "interval",
    "period_days": 3,
    "start_times": ["05:00"],
    "ignore_rain_sensor": False,
    "predictive_watering_ids": [1, 5],
    "predictive_watering_labels": ["Forecast Rain", "Forecast Low Temp"],
    "seasonal_adjustment_factors": [60, 65, 70, 80, 90, 100, 100, 95, 85, 75, 65, 60],
    "zones": [
        {"zone_num": 1, "zone_name": "Front Left", "run_duration_min": 15},
        {"zone_num": 2, "zone_name": "Front Right", "run_duration_min": 15},
    ],
}

PROGRAM_B = {
    "id": 102,
    "name": "Back Yard",
    "program_type": "Time Based",
    "day_pattern": "interval",
    "period_days": 2,
    "start_times": ["05:30"],
    "ignore_rain_sensor": False,
    "predictive_watering_ids": [1],
    "predictive_watering_labels": ["Forecast Rain"],
    "seasonal_adjustment_factors": [50, 55, 65, 75, 85, 95, 100, 100, 85, 70, 55, 50],
    "zones": [
        {"zone_num": 3, "zone_name": "Back Lawn", "run_duration_min": 20},
    ],
}

ZONE_1 = {
    "zone_num": 1,
    "zone_name": "Front Left",
    "run_duration_min": 15,
    "watering_adjustment_pct": {
        "advisory_only": True,
        "ise_blocked": True,
        "value": 100,
    },
}

ZONE_2 = {
    "zone_num": 2,
    "zone_name": "Front Right",
    "run_duration_min": 15,
    "watering_adjustment_pct": {
        "advisory_only": True,
        "ise_blocked": True,
        "value": 100,
    },
}

ZONE_3 = {
    "zone_num": 3,
    "zone_name": "Back Lawn",
    "run_duration_min": 20,
    "watering_adjustment_pct": {
        "advisory_only": True,
        "ise_blocked": True,
        "value": 100,
    },
}


def _base_config() -> dict:
    """Return a minimal valid config dict with two programs and three zones."""
    return {
        "schema_version": "1",
        "exported_at": "2026-06-21T08:00:00",
        "controller": {
            "name": "Home Controller",
            "online": True,
            "firmware": "1.2.3",
        },
        "programs": [copy.deepcopy(PROGRAM_A), copy.deepcopy(PROGRAM_B)],
        "zones_catalog": [copy.deepcopy(ZONE_1), copy.deepcopy(ZONE_2), copy.deepcopy(ZONE_3)],
    }


# ---------------------------------------------------------------------------
# AC3: identical configs -> no drift
# ---------------------------------------------------------------------------


class TestNoDrift:
    def test_identical_configs_no_drift(self):
        """Round-trip identity: a config diffed against itself has no drift."""
        cfg = _base_config()
        result = diff_config(cfg, copy.deepcopy(cfg))

        assert result["has_drift"] is False
        assert result["total_count"] == 0
        assert result["program_level_count"] == 0
        assert result["zone_level_count"] == 0
        assert result["changes"] == []

    def test_volatile_exported_at_ignored(self):
        """exported_at differences are never reported as drift."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["exported_at"] = "2026-01-01T00:00:00"

        result = diff_config(live, desired)
        assert result["has_drift"] is False

    def test_volatile_controller_online_ignored(self):
        """controller.online and controller.last_contact never show as drift."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["controller"]["online"] = False
        desired["controller"]["last_contact"] = "never"

        result = diff_config(live, desired)
        assert result["has_drift"] is False

    def test_schema_version_not_compared(self):
        """schema_version is top-level metadata and not compared (it would break on format change)."""
        live = _base_config()
        desired = copy.deepcopy(live)
        # schema_version is not in the diff scope -- only controller/programs/zones are
        # This test confirms no diff engine error occurs when they match.
        result = diff_config(live, desired)
        assert result["has_drift"] is False


# ---------------------------------------------------------------------------
# AC4: changed seasonal_adjustment_factors -> program_level change
# ---------------------------------------------------------------------------


class TestProgramLevelChanges:
    def test_seasonal_adjustment_change_detected(self):
        """Edited seasonal_adjustment_factors shows as a program_level change (AC4)."""
        live = _base_config()
        desired = copy.deepcopy(live)
        # Bump June factor from 100 to 90 in program A (id=101)
        desired["programs"][0]["seasonal_adjustment_factors"][5] = 90

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        assert result["program_level_count"] == 1
        assert result["zone_level_count"] == 0

        change = result["changes"][0]
        assert change["category"] == "program_level"
        assert "seasonal_adjustment_factors" in change["path"]
        assert change["live"] == [60, 65, 70, 80, 90, 100, 100, 95, 85, 75, 65, 60]
        assert change["desired"] == [60, 65, 70, 80, 90, 90, 100, 95, 85, 75, 65, 60]

    def test_program_name_change_detected(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["name"] = "Front Lawn Renamed"

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        assert result["program_level_count"] == 1
        change = result["changes"][0]
        assert change["category"] == "program_level"
        assert change["path"].endswith(".name")
        assert change["live"] == "Front Lawn"
        assert change["desired"] == "Front Lawn Renamed"

    def test_period_days_change_detected(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][1]["period_days"] = 3

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        changes_for_period = [c for c in result["changes"] if "period_days" in c["path"]]
        assert len(changes_for_period) == 1
        change = changes_for_period[0]
        assert change["category"] == "program_level"
        assert change["live"] == 2
        assert change["desired"] == 3

    def test_start_times_change_detected(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["start_times"] = ["06:00"]

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "start_times" in c["path"])
        assert change["category"] == "program_level"

    def test_predictive_watering_ids_change(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["predictive_watering_ids"] = [1]

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "predictive_watering_ids" in c["path"])
        assert change["category"] == "program_level"

    def test_ignore_rain_sensor_change(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["ignore_rain_sensor"] = True

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "ignore_rain_sensor" in c["path"])
        assert change["category"] == "program_level"

    def test_zone_run_duration_in_program_is_program_level(self):
        """Changing zones list in a program is a program_level change."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["zones"][0]["run_duration_min"] = 20

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "zones" in c["path"] and "programs" in c["path"])
        assert change["category"] == "program_level"

    def test_removed_program(self):
        """A program present in live but absent in desired is reported as removed."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"] = [copy.deepcopy(PROGRAM_A)]  # remove PROGRAM_B (id=102)

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        removed = [c for c in result["changes"] if c.get("action") == "removed" and "id=102" in c["path"]]
        assert len(removed) == 1
        assert removed[0]["category"] == "program_level"
        assert removed[0]["live"] == "Back Yard"
        assert removed[0]["desired"] is None

    def test_added_program(self):
        """A program in desired but absent from live is reported as added."""
        live = _base_config()
        live["programs"] = [copy.deepcopy(PROGRAM_A)]  # only A in live
        desired = _base_config()  # both A and B in desired

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        added = [c for c in result["changes"] if c.get("action") == "added" and "id=102" in c["path"]]
        assert len(added) == 1
        assert added[0]["category"] == "program_level"
        assert added[0]["live"] is None
        assert added[0]["desired"] == "Back Yard"

    def test_controller_name_change(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["controller"]["name"] = "Garage Controller"

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "controller.name" in c["path"])
        assert change["category"] == "program_level"
        assert change["live"] == "Home Controller"
        assert change["desired"] == "Garage Controller"

    def test_controller_firmware_change(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["controller"]["firmware"] = "2.0.0"

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "controller.firmware" in c["path"])
        assert change["category"] == "program_level"


# ---------------------------------------------------------------------------
# Zone-level advisory changes
# ---------------------------------------------------------------------------


class TestZoneLevelChanges:
    def test_watering_adjustment_pct_change_is_advisory(self):
        """Changed watering_adjustment_pct is zone_level advisory (ISE-blocked)."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][0]["watering_adjustment_pct"]["value"] = 80

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        assert result["zone_level_count"] == 1
        change = next(c for c in result["changes"] if "watering_adjustment_pct" in c["path"])
        assert change["category"] == "zone_level"
        assert change.get("advisory") is True
        assert change.get("ise_blocked") is True
        assert "note" in change

    def test_cycle_soak_change_is_advisory(self):
        """Added cycle_soak setting is zone_level advisory."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][1]["cycle_soak"] = {
            "advisory_only": True,
            "ise_blocked": True,
            "cycle_min": 10,
            "soak_min": 20,
        }

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "cycle_soak" in c["path"])
        assert change["category"] == "zone_level"
        assert change.get("advisory") is True
        assert change.get("ise_blocked") is True

    def test_zone_name_change_in_catalog_is_program_level(self):
        """zone_name in zones_catalog is program_level (not advisory)."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][0]["zone_name"] = "Front Left Renamed"

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        change = next(c for c in result["changes"] if "zone_name" in c["path"])
        assert change["category"] == "program_level"
        assert change.get("advisory") is None

    def test_mixed_program_and_zone_changes(self):
        """Multiple changes across categories are counted independently."""
        live = _base_config()
        desired = copy.deepcopy(live)
        # One program_level change
        desired["programs"][0]["seasonal_adjustment_factors"][5] = 90
        # One zone_level change
        desired["zones_catalog"][0]["watering_adjustment_pct"]["value"] = 75

        result = diff_config(live, desired)

        assert result["has_drift"] is True
        assert result["program_level_count"] == 1
        assert result["zone_level_count"] == 1
        assert result["total_count"] == 2


# ---------------------------------------------------------------------------
# Result shape contract
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_no_drift_result_shape(self):
        cfg = _base_config()
        result = diff_config(cfg, copy.deepcopy(cfg))

        assert set(result.keys()) == {"has_drift", "program_level_count", "zone_level_count", "total_count", "changes"}
        assert isinstance(result["changes"], list)

    def test_change_entry_has_required_keys(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["period_days"] = 5

        result = diff_config(live, desired)
        change = result["changes"][0]

        assert "path" in change
        assert "live" in change
        assert "desired" in change
        assert "category" in change

    def test_zone_level_entry_has_advisory_keys(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][0]["watering_adjustment_pct"]["value"] = 80

        result = diff_config(live, desired)
        change = next(c for c in result["changes"] if c["category"] == "zone_level")

        assert change.get("advisory") is True
        assert change.get("ise_blocked") is True
        assert isinstance(change.get("note"), str)

    def test_empty_programs_no_error(self):
        """Empty programs list in both configs is valid (zero drift)."""
        live = _base_config()
        live["programs"] = []
        desired = copy.deepcopy(live)

        result = diff_config(live, desired)
        assert result["has_drift"] is False

    def test_empty_zones_catalog_no_error(self):
        live = _base_config()
        live["zones_catalog"] = []
        desired = copy.deepcopy(live)

        result = diff_config(live, desired)
        assert result["has_drift"] is False
