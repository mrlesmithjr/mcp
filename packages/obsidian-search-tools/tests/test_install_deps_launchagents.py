"""Shell smoke test for the generated hooks/install_deps.sh LaunchAgent block.

This exercises the actual bash script (not just the Python it eventually
calls), because the P0 this guards against -- install_deps.sh aborting under
`set -euo pipefail` on a fresh install with no persisted env file yet -- lived
entirely outside pytest's reach until this test existed. The script is shared
across all packages (generated from scripts/gen_marketplace.py), so a
regression here would affect every tool that ever ships a launchagents/ dir,
not just obsidian-search-tools.

Pre-seeds PLUGIN_DATA's venv/deps.hash so the (slow, network-dependent) venv
build path is skipped and only the LaunchAgent install block under test runs.
`launchctl`/`plutil` are macOS-only; assertions that depend on them are
skipped when those binaries are absent (e.g. the Linux CI runner this package
tests on -- see .github/workflows/ci.yml's test-linux job).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
INSTALL_DEPS_SH = PACKAGE_ROOT / "hooks" / "install_deps.sh"


def _current_deps_hash() -> str:
    """Replicate install_deps.sh's `cat pyproject.toml install_deps.sh | shasum -a 256`."""
    data = (PACKAGE_ROOT / "pyproject.toml").read_bytes() + INSTALL_DEPS_SH.read_bytes()
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def fake_home(tmp_path):
    """A fake HOME/PLUGIN_DATA pair with a pre-seeded venv so only the
    LaunchAgent block is exercised, and deliberately NO
    ~/.config/obsidian-search-tools/env -- the fresh-install path the P0
    fired on."""
    home = tmp_path / "home"
    plugin_data = tmp_path / "plugin-data"
    home.mkdir()
    plugin_data.mkdir()

    # Reuse this test process's own venv (it already has obsidian-search-tools
    # installed editable) instead of letting install_deps.sh build a fresh one.
    (plugin_data / "venv").symlink_to(Path(sys.executable).parent.parent)
    (plugin_data / "deps.hash").write_text(_current_deps_hash())

    assert not (home / ".config" / "obsidian-search-tools" / "env").exists()
    return home, plugin_data


def _run_install_deps(home: Path, plugin_data: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALL_DEPS_SH)],
        env={
            "HOME": str(home),
            "PLUGIN_ROOT": str(PACKAGE_ROOT),
            "PLUGIN_DATA": str(plugin_data),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_fresh_install_completes_without_env_file(fake_home):
    """The P0 regression test: a brand-new install (no env file yet) must not
    abort install_deps.sh before it writes env.example / renders the plist."""
    home, plugin_data = fake_home
    result = _run_install_deps(home, plugin_data)

    assert result.returncode == 0, f"install_deps.sh aborted:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_fresh_install_writes_env_example(fake_home):
    home, plugin_data = fake_home
    _run_install_deps(home, plugin_data)

    assert (home / ".config" / "obsidian-search-tools" / "env.example").is_file()


def test_fresh_install_renders_plist_with_default_schedule(fake_home):
    home, plugin_data = fake_home
    result = _run_install_deps(home, plugin_data)

    plist = home / "Library" / "LaunchAgents" / "com.obsidian-search-tools.reindex.plist"
    assert plist.is_file(), f"plist not rendered:\nstdout={result.stdout}\nstderr={result.stderr}"

    text = plist.read_text()
    assert "__HOME__" not in text
    assert "__START_CALENDAR_INTERVAL__" not in text
    assert "<key>StartCalendarInterval</key>" in text
    assert "<key>RunAtLoad</key>" in text
    assert "<true/>" in text
    # Default cadence (06:00, 12:00, 18:00): 3 entries.
    assert text.count("<key>Hour</key>") == 3


def test_fresh_install_schedule_guard_records_no_env_file_sentinel(fake_home):
    """schedule.hash must be written even though there was nothing to hash,
    so a later session that DOES gain an env file is recognized as a change."""
    home, plugin_data = fake_home
    _run_install_deps(home, plugin_data)

    guard = plugin_data / "schedule.hash"
    assert guard.is_file()
    assert guard.read_text().strip() == "no-env-file"


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS-only")
def test_fresh_install_plist_is_valid_xml(fake_home):
    home, plugin_data = fake_home
    _run_install_deps(home, plugin_data)

    plist = home / "Library" / "LaunchAgents" / "com.obsidian-search-tools.reindex.plist"
    lint = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
