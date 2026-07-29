"""Atomic file writes for lawnops config and state files (issue #73).

config.json holds Hydrawise credentials; irrigation_state.yaml is the
desired-state irrigation snapshot. Both were previously written with a
plain `open(path, "w")`, which can leave a truncated or invalid file behind
if the process is interrupted mid-write (Ctrl-C, OOM-kill, host reboot).

This module writes to a temp file in the same directory, flushes and
fsyncs it, then swaps it onto the target path with os.replace(). The
original file is only ever replaced after a complete, fsynced write
succeeds -- an interrupted write only ever corrupts the abandoned temp
file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any


def atomic_write(
    path: Path | str,
    write_func: Callable[[IO[str]], None],
    mode: int | None = None,
    dir_mode: int | None = None,
) -> None:
    """Write to `path` atomically, using `write_func` to produce the content.

    `write_func` receives an open text-mode file handle for a temp file
    created alongside `path` and should write the desired content to it
    (e.g. `json.dump(data, f)` or `yaml.dump(data, f)`). The temp file is
    flushed, fsynced, and os.replace()'d onto `path` only after
    `write_func` returns without raising. On any exception the temp file
    is removed and `path` is left untouched.

    `mode`, if given, is applied to the temp file BEFORE any content is
    written or the file is replaced onto `path`. This matters for files
    holding secrets (e.g. config.json's Hydrawise credentials): applying
    the mode only after os.replace() would leave the file visible at its
    final path with default, umask-derived permissions for the interval
    between replace and chmod. Because os.replace() is a rename onto the
    same inode, the temp file's mode carries over to `path` -- no chmod on
    `path` itself is needed or performed.

    `dir_mode`, if given, is chmod'd onto `path`'s parent directory
    unconditionally, not only when this call creates the directory. A
    directory created by an older, pre-fix install (or via `mkdir()`
    elsewhere with no explicit mode) would otherwise stay at its original,
    more permissive mode forever.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        target.parent.chmod(dir_mode)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with open(tmp, "w") as f:
            if mode is not None:
                os.chmod(tmp, mode)
            write_func(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(
    path: Path | str,
    data: Any,
    mode: int | None = None,
    dir_mode: int | None = None,
) -> None:
    """Write JSON to `path` atomically. See `atomic_write()` for guarantees."""

    def _write(f: IO[str]) -> None:
        json.dump(data, f, indent=2)
        f.write("\n")

    atomic_write(path, _write, mode=mode, dir_mode=dir_mode)
