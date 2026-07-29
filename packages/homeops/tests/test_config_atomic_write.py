"""Tests for _cmd_configure's atomic config.json write (issue #71).

config.json holds the Home Assistant long-lived token; a write interrupted
mid-json.dump (Ctrl-C, OOM-kill, host reboot) must never leave it truncated
or corrupted. Mirrors mail-tools' gmail_tokens.json/sender_rules.json
temp-file-then-rename tests.
"""

import importlib
import json

import pytest
from homeops.cli.main import _write_config_atomic

# homeops/cli/__init__.py does `from .main import main`, which rebinds the
# `main` attribute on the homeops.cli package to that function - shadowing
# the homeops.cli.main submodule. `from homeops.cli import main as main_mod`
# would therefore resolve to the function, not the module. Pull the module
# straight from sys.modules via importlib instead.
main_mod = importlib.import_module("homeops.cli.main")


def test_write_config_atomic_creates_file_with_expected_content(tmp_path):
    config_file = tmp_path / "config.json"
    data = {"ha_url": "http://ha.local:8123", "ha_token": "secret"}

    _write_config_atomic(config_file, data)

    assert config_file.exists()
    assert json.loads(config_file.read_text()) == data


def test_write_config_atomic_sets_owner_only_permissions(tmp_path):
    config_file = tmp_path / "config.json"
    _write_config_atomic(config_file, {"ha_url": "x", "ha_token": "y"})

    mode = config_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_write_config_atomic_leaves_no_tmp_file_on_success(tmp_path):
    config_file = tmp_path / "config.json"
    _write_config_atomic(config_file, {"ha_url": "x", "ha_token": "y"})

    assert not config_file.with_suffix(".json.tmp").exists()


def test_interrupted_write_leaves_no_partial_file_on_first_run(tmp_path, monkeypatch):
    """First-run case: config.json does not exist yet. A write interrupted
    mid-json.dump must leave it absent, not partially written.
    """
    config_file = tmp_path / "config.json"
    assert not config_file.exists()

    def _boom(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr(main_mod.json, "dump", _boom)

    with pytest.raises(OSError):
        _write_config_atomic(config_file, {"ha_url": "x", "ha_token": "y"})

    assert not config_file.exists()
    assert not config_file.with_suffix(".json.tmp").exists()


def test_interrupted_write_leaves_original_config_untouched(tmp_path, monkeypatch):
    """Existing config.json must survive an interrupted overwrite unchanged -
    the whole point of write-to-temp-then-rename.
    """
    config_file = tmp_path / "config.json"
    original = {"ha_url": "http://original.local:8123", "ha_token": "original-token"}
    config_file.write_text(json.dumps(original))

    def _boom(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr(main_mod.json, "dump", _boom)

    with pytest.raises(OSError):
        _write_config_atomic(config_file, {"ha_url": "http://new.local:8123", "ha_token": "new-token"})

    assert json.loads(config_file.read_text()) == original
    assert not config_file.with_suffix(".json.tmp").exists()
