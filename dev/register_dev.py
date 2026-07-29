#!/usr/bin/env python3
"""Register dev-mode MCP servers with Claude Code (user scope).

Strategy (D4 option a): register each tool as '<tool>-dev' pointing at the
absolute .venv/bin/<script> path so the editable workspace code runs
regardless of what is installed system-wide or via uvx. Marketplace plugins
are NOT installed locally; they are consumer-only.

Pre-requisite: run 'uv sync --all-packages' at the repo root first so the
console scripts exist in .venv/bin/.

Usage:
  uv run python dev/register_dev.py           # Register all tools
  uv run python dev/register_dev.py --list    # Show what would be registered
  uv run python dev/register_dev.py --dry-run # Print commands without running
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (mirrors gen_marketplace.py)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"
VENV_BIN = REPO_ROOT / ".venv" / "bin"

_EXCLUDED_PACKAGES = {"mrlesmithjr-mcp-common", "homeops-coordinator"}
_MCP_ENTRY_SUFFIX = "mcp_server:main"
_PYPI_PREFIX = "mrlesmithjr-mcp-"


# ---------------------------------------------------------------------------
# Discovery (shared logic with gen_marketplace.py)
# ---------------------------------------------------------------------------


def _find_members() -> list[Path]:
    if not PACKAGES_DIR.exists():
        return []
    return sorted(p for p in PACKAGES_DIR.iterdir() if p.is_dir() and (p / "pyproject.toml").exists())


def _load_pyproject(pkg_dir: Path) -> dict:
    with open(pkg_dir / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def _find_mcp_scripts(scripts: dict[str, str]) -> list[str]:
    """Return all script names whose entry point ends with 'mcp_server:main'."""
    return sorted(name for name, ep in scripts.items() if ep.endswith(_MCP_ENTRY_SUFFIX))


def _tool_slug_from_package(pypi_name: str) -> str:
    if pypi_name.startswith(_PYPI_PREFIX):
        return pypi_name[len(_PYPI_PREFIX):]
    return pypi_name


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _collect_registrations() -> list[dict]:
    """Return list of {dev_name, script_name, script_path} for each eligible tool."""
    regs = []
    for pkg_dir in _find_members():
        data = _load_pyproject(pkg_dir)
        project = data.get("project", {})
        pypi_name = project.get("name", "")
        scripts = project.get("scripts", {})

        if pypi_name in _EXCLUDED_PACKAGES:
            continue

        mcp_scripts = _find_mcp_scripts(scripts)
        if not mcp_scripts:
            continue

        tool_slug = _tool_slug_from_package(pypi_name)

        # Register the primary (first, shortest-named) MCP script.
        script_name = sorted(mcp_scripts, key=lambda s: (len(s), s))[0]
        script_path = VENV_BIN / script_name
        dev_name = f"{tool_slug}-dev"

        regs.append(
            {
                "dev_name": dev_name,
                "script_name": script_name,
                "script_path": script_path,
            }
        )

    regs.sort(key=lambda r: r["dev_name"])
    return regs


def _check_prerequisites(regs: list[dict]) -> list[str]:
    """Return list of warning messages for scripts that do not exist yet."""
    warnings = []
    for reg in regs:
        if not reg["script_path"].exists():
            warnings.append(
                f"Script not found: {reg['script_path']}  "
                f"(run 'uv sync --all-packages' first)"
            )
    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register dev-mode MCP servers with Claude Code.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List what would be registered and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print registration commands without executing them.",
    )
    args = parser.parse_args()

    regs = _collect_registrations()
    if not regs:
        print("No eligible tools found.", file=sys.stderr)
        sys.exit(0)

    if args.list:
        print("Dev MCP server registrations:")
        for reg in regs:
            exists = "OK" if reg["script_path"].exists() else "MISSING (run uv sync --all-packages)"
            print(f"  {reg['dev_name']}")
            print(f"    script : {reg['script_path']}  [{exists}]")
        return

    warnings = _check_prerequisites(regs)
    if warnings:
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
        print(
            "\nSome scripts are missing. Register only the scripts that exist? [y/N] ",
            end="",
            file=sys.stderr,
        )
        if not args.dry_run:
            answer = input().strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.", file=sys.stderr)
                sys.exit(1)

    for reg in regs:
        if not reg["script_path"].exists() and not args.dry_run:
            print(f"  SKIP {reg['dev_name']} (script missing)", file=sys.stderr)
            continue

        cmd = [
            "claude",
            "mcp",
            "add",
            "-s",
            "user",
            reg["dev_name"],
            "--",
            str(reg["script_path"]),
        ]

        if args.dry_run:
            print(" ".join(cmd))
        else:
            print(f"  Registering {reg['dev_name']} -> {reg['script_path']}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # 'claude mcp add' exits nonzero when the server already exists.
                # Treat that as a soft warning, not a fatal error.
                stderr_msg = result.stderr.strip()
                if "already exists" in stderr_msg.lower():
                    print(f"    Already registered (skipping): {reg['dev_name']}")
                else:
                    print(f"    ERROR: {stderr_msg}", file=sys.stderr)
            else:
                print("    OK")

    if not args.dry_run:
        print("\nDone. Run 'claude mcp list' to confirm.")


if __name__ == "__main__":
    main()
