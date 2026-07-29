"""Unit tests for Phase 3 irrigation apply logic.

Coverage matrix:
  AC5: dry-run default -- update_program NOT called, plan returned
  AC6: confirm=True -- update_program called with correct args for program_level changes
  AC7: run/stop/suspend/resume NEVER invoked by execute_apply
  AC8: zone-level changes NEVER submitted -- always in skipped list

All tests use mocked update_program (no live Hydrawise connection).
plan_apply() is pure logic (no network calls) -- tested directly.
execute_apply() is tested with a mock for update_program.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

from lawnops.irrigation_config import diff_config, plan_apply

# ---------------------------------------------------------------------------
# Shared fixtures (reuse shapes from test_irrigation_diff)
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
    "seasonal_adjustment_factors": [0, 0, 45, 65, 75, 68, 85, 85, 68, 50, 20, 0],
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
    "seasonal_adjustment_factors": [0, 0, 45, 65, 75, 68, 85, 85, 68, 50, 20, 0],
    "zones": [
        {"zone_num": 3, "zone_name": "Back Lawn", "run_duration_min": 20},
    ],
}

ZONE_1 = {
    "zone_num": 1,
    "zone_name": "Front Left",
    "run_duration_min": 15,
    "watering_adjustment_pct": {"advisory_only": True, "ise_blocked": True, "value": 100},
}
ZONE_2 = {
    "zone_num": 2,
    "zone_name": "Front Right",
    "run_duration_min": 15,
    "watering_adjustment_pct": {"advisory_only": True, "ise_blocked": True, "value": 100},
}
ZONE_3 = {
    "zone_num": 3,
    "zone_name": "Back Lawn",
    "run_duration_min": 20,
    "watering_adjustment_pct": {"advisory_only": True, "ise_blocked": True, "value": 100},
}


def _base_config() -> dict:
    return {
        "schema_version": "1",
        "exported_at": "2026-06-22T08:00:00",
        "controller": {
            "name": "Home Controller",
            "online": True,
            "firmware": "1.2.3",
        },
        "programs": [copy.deepcopy(PROGRAM_A), copy.deepcopy(PROGRAM_B)],
        "zones_catalog": [copy.deepcopy(ZONE_1), copy.deepcopy(ZONE_2), copy.deepcopy(ZONE_3)],
    }


# ---------------------------------------------------------------------------
# AC5: dry-run -- plan_apply returns correct structure, update_program NOT called
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_no_drift_empty_plan(self):
        """Zero drift -> empty updates and skipped lists."""
        cfg = _base_config()
        diff_result = diff_config(cfg, copy.deepcopy(cfg))
        plan = plan_apply(diff_result, cfg)

        assert plan["updates"] == []
        assert plan["skipped"] == []

    def test_seasonal_change_produces_update_entry(self):
        """seasonal_adjustment_factors change -> one update entry with correct kwargs."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["seasonal_adjustment_factors"][6] = 90  # July: 85->90

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        assert upd["program_id"] == 101
        assert upd["program_name"] == "Front Lawn"
        assert "seasonal_adjustment_factors" in upd["fields_applied"]
        assert upd["kwargs"]["seasonal_adjustment_factors"] == desired["programs"][0]["seasonal_adjustment_factors"]

    def test_period_days_change_produces_update_entry(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][1]["period_days"] = 3

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        assert upd["program_id"] == 102
        assert "period_days" in upd["fields_applied"]
        assert upd["kwargs"]["period_days"] == 3

    def test_start_times_change_produces_start_time_kwarg(self):
        """start_times change maps to start_time kwarg (first element)."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["start_times"] = ["06:00"]

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        assert "start_times" in upd["fields_applied"]
        assert upd["kwargs"]["start_time"] == "06:00"

    def test_predictive_watering_ids_change(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["predictive_watering_ids"] = [1]

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        assert "predictive_watering_ids" in upd["fields_applied"]
        assert upd["kwargs"]["schedule_adjustment_ids"] == [1]

    def test_multiple_writable_fields_single_update_call(self):
        """Multiple writable fields on same program -> one update entry, all fields."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["seasonal_adjustment_factors"][6] = 90
        desired["programs"][0]["period_days"] = 4

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        assert "seasonal_adjustment_factors" in upd["fields_applied"]
        assert "period_days" in upd["fields_applied"]
        assert upd["kwargs"]["seasonal_adjustment_factors"] == desired["programs"][0]["seasonal_adjustment_factors"]
        assert upd["kwargs"]["period_days"] == 4

    def test_two_programs_changed_two_update_entries(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["period_days"] = 4
        desired["programs"][1]["period_days"] = 3

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 2
        program_ids = {u["program_id"] for u in plan["updates"]}
        assert program_ids == {101, 102}

    def test_update_program_not_called_in_dry_run(self):
        """AC5: update_program must NOT be called when plan is only computed, not executed."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["period_days"] = 5

        diff_result = diff_config(live, desired)

        with patch("lawnops.irrigation.update_program") as mock_update:
            plan = plan_apply(diff_result, desired)
            # plan_apply is pure logic -- it never calls update_program
            mock_update.assert_not_called()

        assert len(plan["updates"]) == 1


# ---------------------------------------------------------------------------
# AC6: confirm=True -- update_program called with correct args
# ---------------------------------------------------------------------------


class TestExecuteApply:
    def _make_mock_result(self, prog_id: int) -> dict:
        return {
            "program_id": prog_id,
            "name": f"prog-{prog_id}",
            "zones_in_program": [],
            "start_times": [],
            "period_days": 2,
            "seasonal_adjustment_factors": [],
        }

    def test_update_program_called_for_program_level_change(self):
        """AC6: execute_apply calls update_program with correct kwargs."""
        from lawnops.irrigation import execute_apply

        plan = {
            "updates": [
                {
                    "program_id": 101,
                    "program_name": "Front Lawn",
                    "kwargs": {"period_days": 4},
                    "fields_applied": ["period_days"],
                }
            ],
            "skipped": [],
        }

        mock_result = self._make_mock_result(101)
        with patch("lawnops.irrigation.update_program", return_value=mock_result) as mock_update:
            report = execute_apply(plan)

        mock_update.assert_called_once_with(101, period_days=4)
        assert len(report["applied"]) == 1
        assert report["applied"][0]["program_id"] == 101
        assert report["applied"][0]["fields_applied"] == ["period_days"]
        assert report["errors"] == []

    def test_update_program_called_with_seasonal_factors(self):
        """Seasonal adjustment factors passed correctly."""
        from lawnops.irrigation import execute_apply

        factors = [0, 0, 45, 65, 75, 68, 90, 85, 68, 50, 20, 0]
        plan = {
            "updates": [
                {
                    "program_id": 101,
                    "program_name": "Front Lawn",
                    "kwargs": {"seasonal_adjustment_factors": factors},
                    "fields_applied": ["seasonal_adjustment_factors"],
                }
            ],
            "skipped": [],
        }

        with patch("lawnops.irrigation.update_program", return_value=self._make_mock_result(101)) as mock_update:
            report = execute_apply(plan)

        mock_update.assert_called_once_with(101, seasonal_adjustment_factors=factors)
        assert len(report["applied"]) == 1

    def test_update_program_called_multiple_programs(self):
        """Multiple program updates -> update_program called once per program."""
        from lawnops.irrigation import execute_apply

        plan = {
            "updates": [
                {
                    "program_id": 101,
                    "program_name": "Front Lawn",
                    "kwargs": {"period_days": 4},
                    "fields_applied": ["period_days"],
                },
                {
                    "program_id": 102,
                    "program_name": "Back Yard",
                    "kwargs": {"period_days": 3},
                    "fields_applied": ["period_days"],
                },
            ],
            "skipped": [],
        }

        def mock_update_program(prog_id, **kwargs):
            return self._make_mock_result(prog_id)

        with patch("lawnops.irrigation.update_program", side_effect=mock_update_program) as mock_update:
            report = execute_apply(plan)

        assert mock_update.call_count == 2
        call_args_set = [(c.args[0], c.kwargs) for c in mock_update.call_args_list]
        assert (101, {"period_days": 4}) in call_args_set
        assert (102, {"period_days": 3}) in call_args_set
        assert len(report["applied"]) == 2
        assert report["errors"] == []

    def test_update_program_error_captured_not_raised(self):
        """update_program failure is captured in errors list, not raised."""
        from lawnops.irrigation import execute_apply

        plan = {
            "updates": [
                {
                    "program_id": 101,
                    "program_name": "Front Lawn",
                    "kwargs": {"period_days": 4},
                    "fields_applied": ["period_days"],
                }
            ],
            "skipped": [],
        }

        with patch("lawnops.irrigation.update_program", side_effect=RuntimeError("API error")):
            report = execute_apply(plan)

        assert len(report["applied"]) == 0
        assert len(report["errors"]) == 1
        assert report["errors"][0]["program_id"] == 101
        assert "API error" in report["errors"][0]["error"]

    def test_skipped_passed_through_unchanged(self):
        """Skipped list from plan is passed through to report."""
        from lawnops.irrigation import execute_apply

        skipped = [
            {
                "path": "zones_catalog[zone_num=1].watering_adjustment_pct",
                "category": "zone_level",
                "reason": "ISE-blocked",
            }
        ]
        plan = {"updates": [], "skipped": skipped}

        with patch("lawnops.irrigation.update_program") as mock_update:
            report = execute_apply(plan)
            mock_update.assert_not_called()

        assert report["skipped"] == skipped
        assert report["applied"] == []
        assert report["errors"] == []


# ---------------------------------------------------------------------------
# AC7: run/stop/suspend/resume NEVER called by execute_apply
# ---------------------------------------------------------------------------


class TestNoRunStateChanges:
    """AC7: execute_apply must only call update_program; never run/stop/suspend/resume."""

    def _forbidden_patches(self):
        """Return list of forbidden function paths to assert never called."""
        return [
            "lawnops.irrigation.run_zone",
            "lawnops.irrigation.run_all",
            "lawnops.irrigation.stop",
            "lawnops.irrigation.suspend",
            "lawnops.irrigation.resume",
        ]

    def test_no_run_state_calls_on_apply(self):
        """None of run_zone / run_all / stop / suspend / resume are invoked."""
        from lawnops.irrigation import execute_apply

        plan = {
            "updates": [
                {
                    "program_id": 101,
                    "program_name": "Front Lawn",
                    "kwargs": {"period_days": 4},
                    "fields_applied": ["period_days"],
                }
            ],
            "skipped": [],
        }

        mock_result = {
            "program_id": 101,
            "name": "Front Lawn",
            "zones_in_program": [],
            "start_times": [],
            "period_days": 4,
            "seasonal_adjustment_factors": [],
        }

        mocks = {name: MagicMock() for name in self._forbidden_patches()}
        with patch("lawnops.irrigation.update_program", return_value=mock_result):
            with patch("lawnops.irrigation.run_zone", mocks["lawnops.irrigation.run_zone"]):
                with patch("lawnops.irrigation.run_all", mocks["lawnops.irrigation.run_all"]):
                    with patch("lawnops.irrigation.stop", mocks["lawnops.irrigation.stop"]):
                        with patch("lawnops.irrigation.suspend", mocks["lawnops.irrigation.suspend"]):
                            with patch("lawnops.irrigation.resume", mocks["lawnops.irrigation.resume"]):
                                report = execute_apply(plan)

        for name, mock in mocks.items():
            assert mock.call_count == 0, f"{name} should never be called by execute_apply"

        assert len(report["applied"]) == 1

    def test_no_run_state_calls_on_empty_plan(self):
        """Empty plan -> update_program not called and no run-state calls."""
        from lawnops.irrigation import execute_apply

        plan = {"updates": [], "skipped": []}

        with patch("lawnops.irrigation.update_program") as mock_update:
            with patch("lawnops.irrigation.run_zone") as mock_run:
                with patch("lawnops.irrigation.stop") as mock_stop:
                    with patch("lawnops.irrigation.suspend") as mock_suspend:
                        with patch("lawnops.irrigation.resume") as mock_resume:
                            report = execute_apply(plan)

        mock_update.assert_not_called()
        mock_run.assert_not_called()
        mock_stop.assert_not_called()
        mock_suspend.assert_not_called()
        mock_resume.assert_not_called()
        assert report["applied"] == []


# ---------------------------------------------------------------------------
# AC8: zone-level changes ALWAYS in skipped, NEVER in updates
# ---------------------------------------------------------------------------


class TestZoneLevelAlwaysSkipped:
    def test_zone_advisory_change_goes_to_skipped(self):
        """AC8: zone-level (ISE-blocked) changes appear in skipped, never in updates."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][0]["watering_adjustment_pct"]["value"] = 80

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert plan["updates"] == []
        assert len(plan["skipped"]) == 1
        sk = plan["skipped"][0]
        assert sk["category"] == "zone_level"
        assert "ISE" in sk["reason"] or "ise" in sk["reason"].lower() or "blocked" in sk["reason"].lower()

    def test_mixed_zone_and_program_change(self):
        """Zone-level in skipped, program-level in updates -- never mixed."""
        live = _base_config()
        desired = copy.deepcopy(live)
        # program-level change
        desired["programs"][0]["period_days"] = 5
        # zone-level advisory change
        desired["zones_catalog"][0]["watering_adjustment_pct"]["value"] = 80

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        assert plan["updates"][0]["program_id"] == 101
        assert len(plan["skipped"]) == 1
        assert plan["skipped"][0]["category"] == "zone_level"

        # Confirm zone-level not in any update kwargs
        for upd in plan["updates"]:
            assert "watering_adjustment_pct" not in str(upd["kwargs"])
            assert "fixed_watering_adjustment" not in str(upd["kwargs"])

    def test_cycle_soak_change_goes_to_skipped(self):
        """cycle_soak advisory changes go to skipped."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][1]["cycle_soak"] = {
            "advisory_only": True,
            "ise_blocked": True,
            "cycle_min": 10,
            "soak_min": 20,
        }

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert plan["updates"] == []
        assert any("zone_level" == sk["category"] for sk in plan["skipped"])

    def test_zone_update_program_never_called_for_zone_level(self):
        """AC8: update_program is NOT called when only zone-level changes exist."""
        from lawnops.irrigation import execute_apply

        plan = {
            "updates": [],  # zone-level changes produce no updates
            "skipped": [
                {
                    "path": "zones_catalog[zone_num=1].watering_adjustment_pct",
                    "category": "zone_level",
                    "reason": "zone-level write blocked by Hydrawise server-side ISE",
                }
            ],
        }

        with patch("lawnops.irrigation.update_program") as mock_update:
            report = execute_apply(plan)
            mock_update.assert_not_called()

        assert report["applied"] == []
        assert len(report["skipped"]) == 1


# ---------------------------------------------------------------------------
# Non-writable program fields -> skipped
# ---------------------------------------------------------------------------


class TestNonWritableProgramFields:
    def test_name_change_goes_to_skipped(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["name"] = "Front Lawn Renamed"

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert plan["updates"] == []
        sk = plan["skipped"][0]
        assert "not writable" in sk["reason"]

    def test_ignore_rain_sensor_change_goes_to_skipped(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["ignore_rain_sensor"] = True

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert plan["updates"] == []
        assert any("not writable" in sk["reason"] for sk in plan["skipped"])

    def test_day_pattern_change_goes_to_skipped(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["day_pattern"] = "days_of_week"

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert plan["updates"] == []
        assert any("not writable" in sk["reason"] for sk in plan["skipped"])

    def test_program_type_change_goes_to_skipped(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["program_type"] = "Smart Watering"

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert plan["updates"] == []
        assert any("not writable" in sk["reason"] for sk in plan["skipped"])

    def test_nonwritable_and_writable_same_program(self):
        """Non-writable field goes to skipped; writable field still produces update entry."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["name"] = "Renamed"  # non-writable
        desired["programs"][0]["period_days"] = 5  # writable

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        assert plan["updates"][0]["program_id"] == 101
        assert "period_days" in plan["updates"][0]["fields_applied"]

        assert len(plan["skipped"]) == 1
        assert "not writable" in plan["skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# Plan shape contract
# ---------------------------------------------------------------------------


class TestPlanShape:
    def test_update_entry_has_required_keys(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["period_days"] = 5

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        upd = plan["updates"][0]
        assert "program_id" in upd
        assert "program_name" in upd
        assert "kwargs" in upd
        assert "fields_applied" in upd
        assert isinstance(upd["kwargs"], dict)
        assert isinstance(upd["fields_applied"], list)

    def test_skipped_entry_has_required_keys(self):
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["zones_catalog"][0]["watering_adjustment_pct"]["value"] = 75

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        sk = plan["skipped"][0]
        assert "path" in sk
        assert "category" in sk
        assert "reason" in sk

    def test_execute_apply_report_has_required_keys(self):
        from lawnops.irrigation import execute_apply

        plan = {"updates": [], "skipped": []}
        with patch("lawnops.irrigation.update_program"):
            report = execute_apply(plan)

        assert "applied" in report
        assert "skipped" in report
        assert "errors" in report
        assert isinstance(report["applied"], list)
        assert isinstance(report["skipped"], list)
        assert isinstance(report["errors"], list)

    def test_zones_change_produces_add_remove_kwargs(self):
        """Zones list change with added/removed zones maps to add_zone_nums/remove_zone_nums."""
        live = _base_config()
        desired = copy.deepcopy(live)
        # Remove zone 2 from program A, add zone 3
        desired["programs"][0]["zones"] = [
            {"zone_num": 1, "zone_name": "Front Left", "run_duration_min": 15},
            {"zone_num": 3, "zone_name": "Back Lawn", "run_duration_min": 20},
        ]

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        kwargs = upd["kwargs"]
        assert "remove_zone_nums" in kwargs
        assert 2 in kwargs["remove_zone_nums"]
        assert "add_zone_nums" in kwargs
        assert 3 in kwargs["add_zone_nums"]

    def test_run_duration_change_produces_run_duration_min_kwarg(self):
        """run_duration_min change on zones list maps to run_duration_min kwarg."""
        live = _base_config()
        desired = copy.deepcopy(live)
        desired["programs"][0]["zones"][0]["run_duration_min"] = 20

        diff_result = diff_config(live, desired)
        plan = plan_apply(diff_result, desired)

        assert len(plan["updates"]) == 1
        upd = plan["updates"][0]
        assert "run_duration_min" in upd["kwargs"]
        assert upd["kwargs"]["run_duration_min"] == 20
