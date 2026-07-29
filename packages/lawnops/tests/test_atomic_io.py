"""Unit tests for lawnops.atomic_io (issue #73).

Coverage: config.json (Hydrawise credentials) and irrigation_state.yaml
writes must be atomic -- an interrupted write must never leave the
original file truncated or corrupted, and must never leave a stray temp
file behind on success.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml
from lawnops.atomic_io import atomic_write, atomic_write_json
from lawnops.irrigation_config import write_yaml


def test_atomic_write_json_creates_file_with_expected_content(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_json(target, {"hydrawise": {"username": "user@example.com"}})

    assert target.exists()
    assert json.loads(target.read_text()) == {"hydrawise": {"username": "user@example.com"}}
    assert not target.with_suffix(".json.tmp").exists()


def test_atomic_write_json_applies_file_mode(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_json(target, {"a": 1}, mode=0o600)

    assert oct(target.stat().st_mode & 0o777) == oct(0o600)


def test_atomic_write_json_sets_file_mode_before_replace(tmp_path, monkeypatch):
    """Regression test for the chmod-after-replace bug (refs #73): the temp
    file must already carry the restrictive mode BEFORE os.replace() makes
    it visible at the target path. Asserting only the final mode (as
    test_atomic_write_json_applies_file_mode does) would pass even if chmod
    happened after replace, since the file's mode is unchanged by the
    rename -- so this test inspects the temp file's mode from inside a spy
    on os.replace, before the real replace runs.
    """
    target = tmp_path / "config.json"
    captured = {}
    real_replace = os.replace

    def _spy_replace(src, dst):
        captured["mode"] = os.stat(src).st_mode & 0o777
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy_replace)

    atomic_write_json(target, {"a": 1}, mode=0o600)

    assert oct(captured["mode"]) == oct(0o600)


def test_atomic_write_json_chmods_preexisting_directory(tmp_path):
    """dir_mode must chmod the containing directory even when it already
    existed before this call (e.g. an install predating this fix) -- not
    only when this call creates the directory fresh (refs #73)."""
    target_dir = tmp_path / "lawnops"
    target_dir.mkdir()
    target_dir.chmod(0o755)
    target = target_dir / "config.json"

    atomic_write_json(target, {"a": 1}, dir_mode=0o700)

    assert oct(target_dir.stat().st_mode & 0o777) == oct(0o700)


def test_interrupted_write_leaves_original_file_untouched(tmp_path):
    target = tmp_path / "config.json"
    original = {"hydrawise": {"username": "original@example.com", "password": "orig-pass"}}
    atomic_write_json(target, original)

    def _boom(f):
        f.write('{"partial": tr')  # write something, then blow up mid-write
        raise RuntimeError("simulated interrupted write")

    with pytest.raises(RuntimeError, match="simulated interrupted write"):
        atomic_write(target, _boom)

    # Original file must be exactly what it was before the failed write.
    assert json.loads(target.read_text()) == original
    # No leftover temp file.
    assert not target.with_suffix(".json.tmp").exists()


def test_atomic_write_json_no_temp_file_left_behind_on_success(tmp_path):
    target = tmp_path / "nested" / "config.json"
    atomic_write_json(target, {"a": 1})

    tmp_files = list(target.parent.glob("*.tmp"))
    assert tmp_files == []


def test_write_yaml_is_atomic(tmp_path):
    target = tmp_path / "irrigation_state.yaml"
    original_cfg = {"schema_version": 1, "programs": [{"id": 1, "name": "Front Lawn"}]}
    write_yaml(original_cfg, path=target)

    assert yaml.safe_load(target.read_text()) == original_cfg


def test_write_yaml_interrupted_write_preserves_original(tmp_path, monkeypatch):
    target = tmp_path / "irrigation_state.yaml"
    original_cfg = {"schema_version": 1, "programs": [{"id": 1, "name": "Front Lawn"}]}
    write_yaml(original_cfg, path=target)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated interrupted yaml dump")

    monkeypatch.setattr(yaml, "dump", _boom)

    with pytest.raises(RuntimeError, match="simulated interrupted yaml dump"):
        write_yaml({"schema_version": 2, "programs": []}, path=target)

    assert yaml.safe_load(target.read_text()) == original_cfg
    assert not target.with_suffix(".yaml.tmp").exists()
