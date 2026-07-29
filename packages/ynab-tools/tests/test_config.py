"""Tests for configuration loading."""

import json
import os
from unittest.mock import patch

import pytest

from ynab_tools import config
from ynab_tools.config import (
    CATEGORY_DEFS_EXAMPLE_FILE,
    CATEGORY_DEFS_FILE,
    DATA_DIR,
    RULES_FILE,
    atomic_write_json,
)


class TestPaths:
    def test_data_dir_exists(self):
        assert DATA_DIR.exists()

    def test_rules_file_path(self):
        assert RULES_FILE.name == "payee_rules.json"
        assert RULES_FILE.parent == DATA_DIR

    def test_category_defs_path(self):
        assert CATEGORY_DEFS_FILE.name == "category_definitions.json"
        assert CATEGORY_DEFS_FILE.parent == DATA_DIR

    def test_category_defs_example_ships(self):
        """Only the template is packaged; the user copy is gitignored."""
        assert CATEGORY_DEFS_EXAMPLE_FILE.name == "category_definitions.example.json"
        assert CATEGORY_DEFS_EXAMPLE_FILE.exists()

    def test_resolve_prefers_user_copy_then_example(self, tmp_path, monkeypatch):
        user = tmp_path / "category_definitions.json"
        monkeypatch.setattr(config, "CATEGORY_DEFS_FILE", user)
        monkeypatch.setattr(config, "CATEGORY_DEFS_EXAMPLE_FILE", tmp_path / "example.json")

        assert config.resolve_category_defs_file().name == "example.json"
        user.write_text("{}")
        assert config.resolve_category_defs_file() == user

    def test_shipped_example_is_valid_and_carries_no_real_payees(self):
        """The template must parse and must not reappear as a spending profile."""
        data = json.loads(CATEGORY_DEFS_EXAMPLE_FILE.read_text())
        assert data["categories"] and data["split_payees"]
        payees = {p for c in data["categories"].values() for p in c.get("typical_payees", [])}
        # Merchants dropped from the template because they identify a person's
        # insurer, region, or lifestyle rather than illustrating the format.
        assert not payees & {"State Farm", "RaceTrac", "QT", "Lowes Foods", "Ancestry.com"}


class TestAtomicWriteJson:
    """Tests for atomic_write_json (issue #78): temp-file + os.replace so a
    crash mid-write never corrupts the live file."""

    def test_writes_valid_json(self, tmp_path):
        target = tmp_path / "config.json"
        atomic_write_json(target, {"access_token": "tok", "plan_id": "p1"})

        assert target.exists()
        assert json.loads(target.read_text()) == {"access_token": "tok", "plan_id": "p1"}

    def test_no_temp_file_left_behind_on_success(self, tmp_path):
        target = tmp_path / "config.json"
        atomic_write_json(target, {"a": 1})

        tmp_file = target.with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_creates_parent_directory(self, tmp_path):
        target = tmp_path / "nested" / "config.json"
        atomic_write_json(target, {"a": 1}, dir_mode=0o700)

        assert target.exists()
        assert json.loads(target.read_text()) == {"a": 1}

    def test_applies_file_mode(self, tmp_path):
        target = tmp_path / "config.json"
        atomic_write_json(target, {"a": 1}, file_mode=0o600)

        assert oct(target.stat().st_mode)[-3:] == "600"

    def test_interrupted_write_leaves_original_untouched(self, tmp_path, monkeypatch):
        """Simulates a process kill mid-write (Ctrl-C, OOM-kill, host reboot)
        by making json.dump raise partway through the temp-file write. The
        real file must be untouched and still valid JSON."""
        target = tmp_path / "config.json"
        original = {"access_token": "original-9999", "plan_id": "p1"}
        atomic_write_json(target, original)

        monkeypatch.setattr(
            "ynab_tools.config.json.dump",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")),
        )

        with pytest.raises(OSError, match="disk full"):
            atomic_write_json(target, {"access_token": "new-token", "plan_id": "p2"})

        assert json.loads(target.read_text()) == original
        tmp_file = target.with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_interrupted_write_after_fsync_cleans_up_temp_file(self, tmp_path, monkeypatch):
        """A more realistic crash: the temp file has already been fully
        written and fsynced (so it holds a complete, valid copy of the
        data - e.g. an access token) and the failure happens in
        os.replace() itself. The leaked temp file must still be removed,
        not just left empty or partially written (refs #78 code review)."""
        target = tmp_path / "config.json"
        original = {"access_token": "original-9999", "plan_id": "p1"}
        atomic_write_json(target, original)

        real_replace = os.replace

        def _boom(*a, **kw):
            raise OSError("simulated replace failure")

        monkeypatch.setattr("ynab_tools.config.os.replace", _boom)

        with pytest.raises(OSError, match="simulated replace failure"):
            atomic_write_json(target, {"access_token": "new-token", "plan_id": "p2"})

        # Original file untouched.
        assert json.loads(target.read_text()) == original
        # The temp file - which at this point held a full fsynced copy of
        # the new (secret-bearing) data - must be gone, not just empty.
        tmp_file = target.with_suffix(".json.tmp")
        assert not tmp_file.exists()

        monkeypatch.setattr("ynab_tools.config.os.replace", real_replace)

    def test_temp_file_has_correct_permissions_before_replace(self, tmp_path, monkeypatch):
        """The temp file must carry file_mode from the moment it is
        created, not after os.replace() swaps it onto the target path -
        otherwise it (and briefly the target, once replaced) sits at the
        permissive default mode for the whole write+fsync window
        (refs #78 code review, CRITICAL)."""
        target = tmp_path / "config.json"
        captured_mode = {}

        real_replace = os.replace

        def _capture_mode_then_replace(src, dst):
            captured_mode["mode"] = os.stat(src).st_mode & 0o777
            real_replace(src, dst)

        monkeypatch.setattr("ynab_tools.config.os.replace", _capture_mode_then_replace)

        atomic_write_json(target, {"access_token": "tok"}, file_mode=0o600)

        assert captured_mode["mode"] == 0o600
        # And the final file itself is still correctly locked down.
        assert oct(target.stat().st_mode)[-3:] == "600"

    def test_chmods_preexisting_directory(self, tmp_path):
        """dir_mode must be applied even when the directory already exists
        (e.g. an install created before this permission was enforced),
        not only at creation time via mkdir's `mode` argument."""
        existing_dir = tmp_path / "preexisting"
        existing_dir.mkdir(mode=0o777)
        os.chmod(existing_dir, 0o777)  # mkdir's mode is masked by umask; force it wide open
        target = existing_dir / "config.json"

        atomic_write_json(target, {"a": 1}, dir_mode=0o700)

        assert oct(existing_dir.stat().st_mode)[-3:] == "700"


class TestCredentials:
    @patch.dict(os.environ, {"YNAB_ACCESS_TOKEN": "tok", "YNAB_PLAN_ID": "plan-1"}, clear=False)
    def test_plan_id_preferred(self):
        from ynab_tools.config import require_credentials

        token, plan_id = require_credentials()
        assert plan_id == "plan-1"

    @patch.dict(os.environ, {"YNAB_ACCESS_TOKEN": "tok", "YNAB_BUDGET_ID": "budget-1"}, clear=False)
    def test_budget_id_fallback(self):
        # Remove YNAB_PLAN_ID if present so fallback is tested
        env = os.environ.copy()
        env.pop("YNAB_PLAN_ID", None)
        with patch.dict(os.environ, env, clear=True):
            os.environ["YNAB_ACCESS_TOKEN"] = "tok"
            os.environ["YNAB_BUDGET_ID"] = "budget-1"
            from ynab_tools.config import require_credentials

            token, plan_id = require_credentials()
            assert plan_id == "budget-1"

    @patch.dict(
        os.environ,
        {"YNAB_ACCESS_TOKEN": "tok", "YNAB_PLAN_ID": "plan-1", "YNAB_BUDGET_ID": "budget-1"},
        clear=False,
    )
    def test_plan_id_takes_precedence_over_budget_id(self):
        from ynab_tools.config import require_credentials

        token, plan_id = require_credentials()
        assert plan_id == "plan-1"


class TestConfigureShow:
    """Tests for _cmd_configure_show token masking (issue #106).

    _cmd_configure_show must never print an access token in full.
    It must mask all but the last 6 characters with '****'.
    """

    def test_access_token_is_masked(self, monkeypatch, capsys):
        """Access token must be shown as '****' + last 6 chars, never in full. refs #106"""
        fake_token = "my-secret-ynab-token-abcdef"
        monkeypatch.setenv("YNAB_ACCESS_TOKEN", fake_token)
        monkeypatch.setenv("YNAB_PLAN_ID", "plan-test-1")

        from ynab_tools.cli import _cmd_configure_show

        with patch("ynab_tools.config.CONFIG_FILE") as mock_cfg:
            mock_cfg.exists.return_value = False
            _cmd_configure_show()

        out = capsys.readouterr().out
        # Full token must not appear
        assert fake_token not in out
        # Masked form: "****" + last 6 chars of the token
        expected_masked = "****" + fake_token[-6:]
        assert expected_masked in out

    def test_access_token_not_leaked_in_any_line(self, monkeypatch, capsys):
        """No output line should contain the raw access token. refs #106"""
        fake_token = "ynab-test-secret-xyz123456"
        monkeypatch.setenv("YNAB_ACCESS_TOKEN", fake_token)
        monkeypatch.setenv("YNAB_PLAN_ID", "plan-xyz")

        from ynab_tools.cli import _cmd_configure_show

        with patch("ynab_tools.config.CONFIG_FILE") as mock_cfg:
            mock_cfg.exists.return_value = False
            _cmd_configure_show()

        out = capsys.readouterr().out
        for line in out.splitlines():
            assert fake_token not in line, f"Token leaked in output line: {line!r}"

    def test_short_token_shows_only_stars(self, monkeypatch, capsys):
        """Token with 6 or fewer chars shows as bare '****'. refs #106"""
        fake_token = "short"  # len <= 6
        monkeypatch.setenv("YNAB_ACCESS_TOKEN", fake_token)
        monkeypatch.setenv("YNAB_PLAN_ID", "plan-abc")

        from ynab_tools.cli import _cmd_configure_show

        with patch("ynab_tools.config.CONFIG_FILE") as mock_cfg:
            mock_cfg.exists.return_value = False
            _cmd_configure_show()

        out = capsys.readouterr().out
        assert fake_token not in out
        assert "****" in out

    def test_masked_token_shows_correct_suffix(self, monkeypatch, capsys):
        """The last 6 chars of the token are exposed so the user can identify it. refs #106"""
        fake_token = "abcdefghij123456"  # last 6: "123456"
        monkeypatch.setenv("YNAB_ACCESS_TOKEN", fake_token)
        monkeypatch.setenv("YNAB_PLAN_ID", "plan-1")

        from ynab_tools.cli import _cmd_configure_show

        with patch("ynab_tools.config.CONFIG_FILE") as mock_cfg:
            mock_cfg.exists.return_value = False
            _cmd_configure_show()

        out = capsys.readouterr().out
        assert "****123456" in out
