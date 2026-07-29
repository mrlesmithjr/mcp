"""Tests for atomic writes in payees/backup.py and payees/normalize.py (issue #78)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from ynab_tools.payees import backup as backup_module
from ynab_tools.payees import normalize as normalize_module


class TestBackupAtomicWrite:
    def test_create_backup_writes_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_module, "BACKUPS_DIR", tmp_path)
        fixes = [
            {
                "ynab_transaction_id": "t1",
                "date": "2026-01-01",
                "amount": -10.0,
                "payee_name": "Old Name",
                "correct_payee": "New Name",
                "category_name": "Groceries",
                "correct_category": None,
            }
        ]

        filename = backup_module.create_backup(fixes)

        written = tmp_path / filename
        assert written.exists()
        data = json.loads(written.read_text())
        assert data["transaction_count"] == 1
        assert not (tmp_path / (filename + ".tmp")).exists()

    def test_interrupted_write_leaves_no_partial_backup(self, tmp_path, monkeypatch):
        """A crash mid-write (mocked json.dump failure) must never leave a
        truncated/invalid backup file on disk."""
        monkeypatch.setattr(backup_module, "BACKUPS_DIR", tmp_path)
        fixes = [
            {
                "ynab_transaction_id": "t1",
                "date": "2026-01-01",
                "amount": -10.0,
                "payee_name": "Old Name",
                "correct_payee": "New Name",
                "category_name": "Groceries",
                "correct_category": None,
            }
        ]

        with patch("ynab_tools.config.json.dump", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                backup_module.create_backup(fixes)

        # No completed backup file, and no leftover non-empty temp file.
        assert list(tmp_path.glob("backup_*.json")) == []
        leftover_tmp = list(tmp_path.glob("*.tmp"))
        assert all(f.stat().st_size == 0 for f in leftover_tmp)


class TestNormalizeAtomicWrite:
    def _duplicate_payees(self):
        return [{"id": "p1", "name": "Kroger #123"}, {"id": "p2", "name": "Kroger #456"}]

    def test_cmd_normalize_writes_valid_backup_before_renaming(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(normalize_module, "BACKUPS_DIR", tmp_path)
        mock_client = MagicMock()
        mock_client.get_payees.return_value = {"payees": self._duplicate_payees()}
        mock_client.update_payee.return_value = True

        with patch("builtins.input", side_effect=["y", "y"]):
            normalize_module.cmd_normalize(mock_client, apply=True)

        backups = list(tmp_path.glob("normalize_backup_*.json"))
        assert len(backups) == 1
        data = json.loads(backups[0].read_text())
        assert data["type"] == "normalization"
        assert not backups[0].with_suffix(".json.tmp").exists()
        assert mock_client.update_payee.call_count == 2

    def test_cmd_normalize_interrupted_backup_write_reports_error_and_skips_renaming(
        self, tmp_path, monkeypatch, capsys
    ):
        """A crash mid-write during the backup step (mocked json.dump
        failure) must be reported and must not proceed to rename payees -
        and must never leave a truncated/invalid backup file on disk."""
        monkeypatch.setattr(normalize_module, "BACKUPS_DIR", tmp_path)
        mock_client = MagicMock()
        mock_client.get_payees.return_value = {"payees": self._duplicate_payees()}

        with (
            patch("builtins.input", side_effect=["y", "y"]),
            patch("ynab_tools.config.json.dump", side_effect=OSError("disk full")),
        ):
            normalize_module.cmd_normalize(mock_client, apply=True)

        out = capsys.readouterr().out
        assert "Failed to write backup" in out
        mock_client.update_payee.assert_not_called()

        assert list(tmp_path.glob("normalize_backup_*.json")) == []
        leftover_tmp = list(tmp_path.glob("*.tmp"))
        assert all(f.stat().st_size == 0 for f in leftover_tmp)
