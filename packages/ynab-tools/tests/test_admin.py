"""Tests for ynab_tools/dashboard/api/admin.py - GET/PUT /admin/config."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ynab_tools.dashboard.api.admin import router

# Build a minimal FastAPI app containing only the admin router
app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestGetAdminConfig:
    def test_config_file_does_not_exist(self, tmp_path):
        missing = tmp_path / "no_config.json"
        with patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", missing):
            resp = client.get("/admin/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False
        assert data["config"] == {}

    def test_config_file_exists_with_access_token_masked(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"access_token": "abcd1234", "plan_id": "p1"}))
        with patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file):
            resp = client.get("/admin/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        # Token should be masked: ****{last4}
        assert data["config"]["access_token"] == "****1234"
        # Other fields pass through
        assert data["config"]["plan_id"] == "p1"

    def test_config_file_exists_without_access_token_returned_as_is(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"plan_id": "p2", "regular_pay": "5000"}))
        with patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file):
            resp = client.get("/admin/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        assert data["config"]["plan_id"] == "p2"
        assert "access_token" not in data["config"]

    def test_config_token_short_less_than_four_chars_masked_fully(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"access_token": "ab"}))
        with patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file):
            resp = client.get("/admin/config")
        data = resp.json()
        # Token is 2 chars, len <= 4, so masked as "****"
        assert data["config"]["access_token"] == "****"


class TestPutAdminConfig:
    def test_writes_new_config_to_file(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        new_config = {"access_token": "mytoken1234", "plan_id": "plan-x"}

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config") as mock_apply,
        ):
            resp = client.put("/admin/config", json={"config": new_config})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # File should have been written
        assert cfg_file.exists()
        written = json.loads(cfg_file.read_text())
        assert written["access_token"] == "mytoken1234"
        assert written["plan_id"] == "plan-x"
        mock_apply.assert_called_once()

    def test_masked_token_preserves_existing_token(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        # Existing file has the real token
        cfg_file.write_text(json.dumps({"access_token": "realtoken9999", "plan_id": "p1"}))

        # Submit with masked token (as would come from a GET-then-PUT flow)
        masked_config = {"access_token": "****9999", "plan_id": "p1"}

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config"),
        ):
            resp = client.put("/admin/config", json={"config": masked_config})

        assert resp.status_code == 200
        written = json.loads(cfg_file.read_text())
        # Existing real token must be preserved - not overwritten with "****9999"
        assert written["access_token"] == "realtoken9999"

    def test_unmasked_new_token_is_written(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"access_token": "oldtoken", "plan_id": "p1"}))

        new_config = {"access_token": "newtoken5678", "plan_id": "p1"}

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config"),
        ):
            resp = client.put("/admin/config", json={"config": new_config})

        assert resp.status_code == 200
        written = json.loads(cfg_file.read_text())
        # New plain token replaces old
        assert written["access_token"] == "newtoken5678"

    def test_masked_token_no_existing_file_writes_empty_token(self, tmp_path):
        cfg_file = tmp_path / "nonexistent.json"
        # File does not exist; submit masked token - falls back to empty string
        masked_config = {"access_token": "****abcd", "plan_id": "p1"}

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config"),
        ):
            resp = client.put("/admin/config", json={"config": masked_config})

        assert resp.status_code == 200
        written = json.loads(cfg_file.read_text())
        # No existing file means existing token is empty string
        assert written["access_token"] == ""

    def test_apply_config_called_with_written_config(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        payload_config = {"access_token": "tok123", "plan_id": "p-abc"}

        captured_call = {}

        def capture_apply(cfg):
            captured_call["cfg"] = cfg

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config", side_effect=capture_apply),
        ):
            client.put("/admin/config", json={"config": payload_config})

        assert captured_call["cfg"]["access_token"] == "tok123"
        assert captured_call["cfg"]["plan_id"] == "p-abc"

    def test_write_is_atomic_no_temp_file_left_behind(self, tmp_path):
        """After a successful write, no stray .json.tmp file should remain
        alongside config.json (issue #78)."""
        cfg_file = tmp_path / "config.json"
        new_config = {"access_token": "mytoken1234", "plan_id": "plan-x"}

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config"),
        ):
            resp = client.put("/admin/config", json={"config": new_config})

        assert resp.status_code == 200
        assert cfg_file.exists()
        assert not cfg_file.with_suffix(".json.tmp").exists()

    def test_interrupted_write_leaves_original_config_untouched(self, tmp_path):
        """Simulates a process kill mid-write (Ctrl-C, OOM-kill, host reboot)
        by making json.dump raise partway through the temp-file write. The
        real config.json - which holds the YNAB access token - must be
        untouched and still valid (issue #78)."""
        cfg_file = tmp_path / "config.json"
        original = {"access_token": "original-token-9999", "plan_id": "plan-orig"}
        cfg_file.write_text(json.dumps(original) + "\n")

        new_config = {"access_token": "new-token-1234", "plan_id": "plan-new"}

        with (
            patch("ynab_tools.dashboard.api.admin.CONFIG_FILE", cfg_file),
            patch("ynab_tools.dashboard.api.admin.apply_config"),
            patch(
                "ynab_tools.config.json.dump",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            client.put("/admin/config", json={"config": new_config})

        # The original file on disk must remain intact and parseable.
        assert json.loads(cfg_file.read_text()) == original
        tmp_file = cfg_file.with_suffix(".json.tmp")
        assert not tmp_file.exists() or tmp_file.stat().st_size == 0
