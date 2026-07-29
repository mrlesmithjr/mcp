"""Workspace-wide pytest setup.

Redirects HOME to a throwaway directory for the whole test session so the
suite can never read or write the developer's real dotfiles.

Why this is needed: `mcp_common.paths.config_dir()`/`data_dir()` create their
directory on access (mode 0700), and several modules resolve paths at import
time rather than call time (e.g. ynab-tools' `config.py:_get_data_dir()`).
Running the suite therefore used to create real directories under `~/.config`
and `~/.local/share` - `~/.config/test-mcp-common-paths-tool` being the most
obvious tell. That is a problem for contributors and CI runners, and a
correctness hazard: a test that silently picks up a real `config.json` or
token file passes or fails based on the machine it runs on.

This runs at import time, not as a fixture, because a fixture executes after
test modules are imported - too late for the import-time path resolution
described above. `Path.home()` delegates to `os.path.expanduser("~")`, which
honours $HOME on POSIX, so setting the variable is sufficient to redirect
both it and anything building paths from it.

Tests that need to assert against a specific home layout should still
monkeypatch `Path.home` themselves; this is a backstop, not a replacement.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_FAKE_HOME = tempfile.mkdtemp(prefix="mcp-workspace-test-home-")

os.environ["HOME"] = _FAKE_HOME
# Some tooling prefers XDG paths; point them inside the sandbox too so a
# stray lookup cannot escape to the real home.
os.environ["XDG_CONFIG_HOME"] = os.path.join(_FAKE_HOME, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(_FAKE_HOME, ".local", "share")


@atexit.register
def _cleanup_fake_home() -> None:
    shutil.rmtree(_FAKE_HOME, ignore_errors=True)
