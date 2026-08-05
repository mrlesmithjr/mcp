"""Atomic file writes for the home_log markdown backend (issue #58).

Writes to a temp file in the same directory, flushes and fsyncs it, then
swaps it onto the target path with os.replace(). The original file is only
ever replaced after a complete, fsynced write succeeds -- an interrupted
write (Ctrl-C, OOM-kill, host reboot) only ever corrupts the abandoned temp
file, never the file the markdown backend already trusts as source of
truth. Ported from lawnops' atomic_io.py (issue #57), same contract.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import IO


def atomic_write(
    path: Path | str,
    write_func: Callable[[IO[str]], None],
    mode: int | None = None,
    dir_mode: int | None = None,
) -> None:
    """Write to `path` atomically, using `write_func` to produce the content.

    `write_func` receives an open text-mode file handle for a temp file
    created alongside `path` and should write the desired content to it. The
    temp file is flushed, fsynced, and os.replace()'d onto `path` only after
    `write_func` returns without raising. On any exception the temp file is
    removed and `path` is left untouched.

    `mode`, if given, is applied to the temp file BEFORE any content is
    written or the file is replaced onto `path`.

    `dir_mode`, if given, is chmod'd onto `path`'s parent directory
    unconditionally, not only when this call creates the directory.
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
