"""Subprocess wrapper and output parser for launchctl.

All subprocess calls live here. The parser functions are pure
(no I/O) so they can be unit-tested with captured fixtures.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

from launchd_tools.config import get_label_prefixes

# ---------------------------------------------------------------------------
# Low-level runner
# ---------------------------------------------------------------------------


def _run(args: list[str]) -> str:
    """Run launchctl with the given args list. Never uses shell=True.

    Returns combined stdout. Raises subprocess.CalledProcessError on failure.
    """
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,  # we check manually below
    )
    if result.returncode != 0:
        raise RuntimeError(f"launchctl {args[1:]} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------------------
# list_agents -- parse `launchctl list`
# ---------------------------------------------------------------------------


def list_agents() -> list[dict]:
    """Return filtered agent list from `launchctl list`.

    Each entry: {label, pid, last_exit_code}
    pid is an int when running, None when idle ("-").
    last_exit_code is an int.
    """
    output = _run(["launchctl", "list"])
    prefixes = get_label_prefixes()
    return _parse_list(output, prefixes)


def _parse_list(output: str, prefixes: list[str]) -> list[dict]:
    """Pure parser for `launchctl list` tabular output.

    Format:  PID<TAB>Status<TAB>Label
    First line is the header; skip it.
    """
    agents: list[dict] = []
    lines = output.splitlines()
    for line in lines[1:]:  # skip header
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        pid_str, status_str, label = parts
        if not any(label.startswith(p) for p in prefixes):
            continue
        pid: int | None = None if pid_str.strip() == "-" else int(pid_str.strip())
        try:
            last_exit_code = int(status_str.strip())
        except ValueError:
            last_exit_code = -1
        agents.append({"label": label.strip(), "pid": pid, "last_exit_code": last_exit_code})
    return agents


# ---------------------------------------------------------------------------
# print_agent -- fetch `launchctl print` for one label
# ---------------------------------------------------------------------------


def print_agent(label: str) -> str:
    """Return raw `launchctl print gui/<uid>/<label>` output."""
    uid = os.getuid()
    return _run(["launchctl", "print", f"gui/{uid}/{label}"])


# ---------------------------------------------------------------------------
# parse_print -- pure parser for `launchctl print` output
# ---------------------------------------------------------------------------


def parse_print(text: str) -> dict:
    """Parse the output of `launchctl print gui/<uid>/<label>`.

    Extracts a narrow set of useful fields via line-oriented scanning.
    Does NOT attempt to parse the full nested block structure -- that
    approach is fragile across macOS versions.

    Returned dict keys (all optional except label):
      state, pid, runs, last_exit_code, stdout_path, stderr_path,
      program, has_event_triggers
    """
    result: dict = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Detect event triggers block (signals scheduled/calendar job).
        # We only need to know whether the block exists; the opening line is
        # sufficient. No brace tracking needed.
        if line == "event triggers = {":
            result["has_event_triggers"] = True
            continue

        # state = running | not running
        # Only capture the top-level state (first occurrence; nested coalitions
        # also have "state = active" lines which must not overwrite it).
        if line.startswith("state = ") and "state" not in result:
            result["state"] = line[len("state = ") :]
            continue

        # pid = <int>  (only present when running)
        if line.startswith("pid = "):
            try:
                result["pid"] = int(line[len("pid = ") :])
            except ValueError:
                pass
            continue

        # runs = <int>
        if line.startswith("runs = "):
            try:
                result["runs"] = int(line[len("runs = ") :])
            except ValueError:
                pass
            continue

        # last exit code = (never exited)  |  last exit code = <int>
        if line.startswith("last exit code = "):
            raw_val = line[len("last exit code = ") :]
            if raw_val == "(never exited)":
                result["last_exit_code"] = None
            else:
                try:
                    result["last_exit_code"] = int(raw_val)
                except ValueError:
                    result["last_exit_code"] = None
            continue

        # stdout path = <path>
        if line.startswith("stdout path = "):
            result["stdout_path"] = line[len("stdout path = ") :]
            continue

        # stderr path = <path>
        if line.startswith("stderr path = "):
            result["stderr_path"] = line[len("stderr path = ") :]
            continue

        # program = <path>  (first occurrence is the program executable)
        if line.startswith("program = ") and "program" not in result:
            result["program"] = line[len("program = ") :]
            continue

        # properties line -- used by health classifier
        if line.startswith("properties = "):
            result["properties"] = line[len("properties = ") :]
            continue

    return result


# ---------------------------------------------------------------------------
# Log tailing
# ---------------------------------------------------------------------------


def tail_file(path: str, n: int = 50) -> list[str]:
    """Return the last n lines of a file. Returns [] if file not found."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        lines = p.read_text(errors="replace").splitlines()
        return lines[-n:] if len(lines) > n else lines
    except OSError:
        return []


def infer_last_run(stdout_path: str | None, stderr_path: str | None) -> str | None:
    """Return ISO8601 mtime of the most-recently-modified log file, or None."""
    candidates = [p for p in (stdout_path, stderr_path) if p]
    best: float | None = None
    for candidate in candidates:
        try:
            mtime = Path(candidate).stat().st_mtime
            if best is None or mtime > best:
                best = mtime
        except OSError:
            continue
    if best is None:
        return None
    return datetime.datetime.fromtimestamp(best, tz=datetime.timezone.utc).isoformat()
