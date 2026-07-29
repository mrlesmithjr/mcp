#!/usr/bin/env python3
"""Generate plugin metadata for the Claude Code and Codex plugin marketplaces.

Discovers workspace members by reading packages/*/pyproject.toml, identifies
the primary MCP console script (the one whose entry point ends with
'mcp_server:main'), and emits the following files directly inside each tool's
package directory (alongside the Python source, which uv ignores):

  packages/<tool>/.claude-plugin/plugin.json  -- Claude plugin metadata (no hooks field)
  packages/<tool>/hooks/hooks.json            -- hook wiring (SessionStart via auto-discover)
  packages/<tool>/.mcp.json                   -- Claude Code MCP server definition
  packages/<tool>/hooks/install_deps.sh       -- idempotent venv builder
  packages/<tool>/hooks/run_server.sh         -- self-bootstrapping server launcher
  packages/<tool>/.codex-plugin/plugin.json   -- Codex plugin manifest (inline MCP definition)

At the workspace root:

  .claude-plugin/marketplace.json             -- full Claude marketplace index
  .agents/plugins/marketplace.json            -- full Codex marketplace index
  README.md                                   -- plugin-table Version column only

When a plugin is installed via 'claude plugin install', Claude clones the tool's
packages/<tool>/ directory into ~/.claude/plugins/cache/{id}/{ver}/ and exposes
it as ${CLAUDE_PLUGIN_ROOT}. The persistent data directory (venv, manifests) is
kept at ${CLAUDE_PLUGIN_DATA} and survives updates. Codex clones the same
packages/<tool>/ directory via 'codex plugin add'. Its manifest carries an
inline MCP definition with cwd "." so Codex resolves the launcher relative to
the installed plugin root. The Claude manifest continues to use .mcp.json and
its ${CLAUDE_PLUGIN_ROOT} runtime template unchanged.

Hook wiring: hooks are declared in hooks/hooks.json (auto-discovered by the Claude
plugin runtime), NOT as a field in plugin.json. plugin.json carries only name/version/
description/author/repository/mcpServers. Codex's plugin.json has no hooks
equivalent at all -- see .codex-plugin/plugin.json below and the Codex plugin
pattern section of mcp/CLAUDE.md.

Output is idempotent (sorted, deterministic). Re-running on unchanged input
produces byte-identical files. Scripts are written as regular files (not
executable bits set here; the plugin runtime sets them at install time).

Usage:
  python scripts/gen_marketplace.py           # Generate files in place
  python scripts/gen_marketplace.py --check   # Exit nonzero if any file is stale
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repo root = parent of this script's directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"
MARKETPLACE_FILE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE_FILE = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
README_FILE = REPO_ROOT / "README.md"

# Matches a README plugin-table row: | `<name>` | <version> | <description> |
# Captures the surrounding cells so only the version cell is rewritten and the
# hand-curated name + description columns are preserved byte-for-byte. The
# non-plugin rows (mcp-common "(library)", homeops-coordinator "(daemon)") are
# left untouched because their names are not in the discovered tools list.
_README_ROW_RE = re.compile(r"^(\| `)([^`]+)(` \| )([^|]+?)( \| .*)$")

# Packages that carry no MCP server and must be excluded from the marketplace.
# mcp-common is a shared library; homeops-coordinator is a coordinator daemon.
_EXCLUDED_PACKAGES = {"mrlesmithjr-mcp-common", "homeops-coordinator"}

# Convention: the MCP entry point must end with this suffix.
_MCP_ENTRY_SUFFIX = "mcp_server:main"

# PyPI package prefix applied to all tool packages.
_PYPI_PREFIX = "mrlesmithjr-mcp-"

# Marketplace owner metadata (mirrors context-manager schema).
# The marketplace name MUST be distinct from the context-manager marketplace
# ("mrlesmithjr"), or adding this one overwrites that one and breaks the
# context-manager plugin's linkage. Keep these two marketplaces separate.
#
# _OWNER["name"] is ALSO reused verbatim as the Codex marketplace name in
# .agents/plugins/marketplace.json (see _codex_marketplace()). That reuse is
# intentional, not the collision this comment warns against: Claude and Codex
# read two different manifest files (.claude-plugin/marketplace.json vs.
# .agents/plugins/marketplace.json) in two different ecosystems, so the same
# "mrlesmithjr-mcp" name in both does not create the cross-marketplace
# overwrite risk described above. The issue's acceptance criteria hardcode
# `codex plugin add unifi-tools@mrlesmithjr-mcp`, so this name is a hard
# constraint on the Codex side too.
_OWNER = {
    "name": "mrlesmithjr-mcp",
    "owner": {
        "name": "Larry Smith Jr.",
    },
    "metadata": {
        "description": "Personal MCP servers by Larry Smith Jr.",
        "homepage": "https://github.com/mrlesmithjr/mcp",
    },
}

# The deps-hash command, shared verbatim by install_deps.sh (which writes the
# guard) and run_server.sh (which compares against it before exec-ing). Injected
# as a .format() argument, NOT a template literal, so its single braces survive
# brace-escaping -- and so the two scripts can never compute the hash differently
# (a drift would make run_server.sh rebuild on every launch). Reads the same two
# inputs the guard is keyed on: the dependency spec and the installer itself.
# Requires PYPROJECT and PLUGIN_ROOT to be set in the calling script.
_DEPS_HASH_CMD = 'cat "${PYPROJECT}" "${PLUGIN_ROOT}/hooks/install_deps.sh" | shasum -a 256 | awk \'{print $1}\''

# install_deps.sh: idempotent venv builder, used by both the SessionStart hook and
# run_server.sh. Variables expanded at generation time: {tool_slug}.
# Variables resolved at run time by the plugin runtime or inferred by the script:
#   PLUGIN_ROOT  -- the installed package dir (packages/<tool>/ clone)
#   PLUGIN_DATA  -- persistent per-plugin data dir (survives updates)
_INSTALL_DEPS_TEMPLATE = textwrap.dedent("""\
    #!/usr/bin/env bash
    # install_deps.sh: install or update the tool's persistent plugin venv.
    #
    # Codex supplies PLUGIN_ROOT/PLUGIN_DATA and compatibility aliases; Claude
    # supplies CLAUDE_PLUGIN_ROOT/CLAUDE_PLUGIN_DATA. Normalize both runtimes,
    # with deterministic fallbacks so direct invocation works as well.
    #
    # The guard file (deps.hash) stores a SHA-256 of pyproject.toml so the venv
    # is only rebuilt when dependencies actually change.
    set -euo pipefail

    PLUGIN_ROOT="${{PLUGIN_ROOT:-${{CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")/.." && pwd)}}}}"
    PLUGIN_DATA="${{PLUGIN_DATA:-${{CLAUDE_PLUGIN_DATA:-${{XDG_DATA_HOME:-${{HOME}}/.local/share}}/{tool_slug}}}}}"
    export PLUGIN_ROOT PLUGIN_DATA
    export CLAUDE_PLUGIN_ROOT="${{PLUGIN_ROOT}}" CLAUDE_PLUGIN_DATA="${{PLUGIN_DATA}}"

    VENV_DIR="${{PLUGIN_DATA}}/venv"
    GUARD="${{PLUGIN_DATA}}/deps.hash"
    PYPROJECT="${{PLUGIN_ROOT}}/pyproject.toml"

    # Codex does not pre-create PLUGIN_DATA the way Claude Code's plugin runtime
    # does, so the build.lock mkdir below can fail with a missing parent dir.
    mkdir -p "${{PLUGIN_DATA}}"

    # Hash the dependency spec AND this installer (so a change to the install
    # target, e.g. adding a runtime extra, invalidates the guard and rebuilds).
    CURRENT_HASH=$({deps_hash_cmd})

    # Rebuild the venv only when the dependency spec or this installer changes.
    NEEDS_BUILD=1
    if [ -f "${{GUARD}}" ] && [ "$(cat "${{GUARD}}")" = "${{CURRENT_HASH}}" ]; then
        NEEDS_BUILD=0
    fi

    if [ "${{NEEDS_BUILD}}" = "1" ]; then
        # Serialize rebuilds with a mkdir lock. The SessionStart hook and
        # run_server.sh can both decide to rebuild at the same session start (the
        # latter now rebuilds a stale venv before exec). Without serialization
        # their atomic swaps collide: two `mv venv.tmp venv` race and the second,
        # finding venv/ already present, nests its tree *inside* the first. mkdir
        # is atomic, so exactly one builder proceeds; the other waits, re-checks
        # the hash, and finds the venv already current. A lock held by a dead PID
        # (a builder that was killed) is stolen; a live lock is waited on, with a
        # ~2min cap before forcing past a wedged holder.
        LOCK="${{PLUGIN_DATA}}/build.lock"
        _waited=0
        while ! mkdir "${{LOCK}}" 2>/dev/null; do
            _lpid="$(cat "${{LOCK}}/pid" 2>/dev/null || true)"
            if [ -n "${{_lpid}}" ] && ! kill -0 "${{_lpid}}" 2>/dev/null; then
                rm -rf "${{LOCK}}" 2>/dev/null || true
                continue
            fi
            _waited=$((_waited + 1))
            if [ "${{_waited}}" -gt 400 ]; then
                # Wedged holder: force past it, then reset and fall through to the
                # sleep so a holder that immediately re-creates the lock cannot
                # spin us in a tight rm/mkdir loop.
                rm -rf "${{LOCK}}" 2>/dev/null || true
                _waited=0
            fi
            sleep 0.3
        done
        echo "$$" > "${{LOCK}}/pid"
        # From here on, always release the lock (and drop any in-progress temp
        # dir) on exit, so a crash mid-build leaves the existing venv and guard
        # untouched and does not wedge the lock.
        trap 'rm -rf "${{VENV_TMP:-}}" "${{LOCK}}"' EXIT

        # Re-check under the lock: a builder we waited behind may have already
        # made the venv current, in which case there is nothing to build.
        if [ -f "${{GUARD}}" ] && [ "$(cat "${{GUARD}}")" = "${{CURRENT_HASH}}" ]; then
            NEEDS_BUILD=0
        else
            # Build the venv in a private temp dir, then atomically swap it into
            # place. A destructive in-place `uv venv --clear` would leave a
            # half-built tree that an importing server can observe (issue #22);
            # building in a per-PID temp dir and renaming means a server reading
            # venv/ only ever sees the previous complete venv or the new one.
            # --relocatable lets the finished venv be renamed without breaking the
            # absolute paths uv bakes into the interpreter config and
            # console-script shebangs. mrlesmithjr-mcp-common resolves from PyPI.
            VENV_TMP="${{PLUGIN_DATA}}/venv.tmp.$$"
            # Reclaim leftovers: an interrupted build's temp dir, and superseded
            # venvs from a prior session. The dir suffix is the PID of the builder
            # that created it; skip any whose process is still alive (defense in
            # depth -- the lock already excludes a concurrent builder).
            for _leftover in "${{PLUGIN_DATA}}"/venv.tmp.* "${{PLUGIN_DATA}}"/venv.old.*; do
                [ -e "${{_leftover}}" ] || continue
                _opid="${{_leftover##*.}}"
                case "${{_opid}}" in
                    ''|*[!0-9]*) ;;
                    *) if kill -0 "${{_opid}}" 2>/dev/null; then continue; fi ;;
                esac
                rm -rf "${{_leftover}}"
            done
            uv venv --relocatable --python python3 "${{VENV_TMP}}"
            uv pip install --python "${{VENV_TMP}}/bin/python" {install_target}
            # Atomic swap: rename the current venv aside, then rename the freshly
            # built one into place. Each mv is a single atomic rename. The
            # superseded venv is NOT deleted here: a server already launched from
            # it this session may still be importing, and removing it underneath
            # would break those imports. It is reclaimed by the leftover-clear
            # above on the next rebuild, after that session has ended.
            if [ -d "${{VENV_DIR}}" ]; then
                mv "${{VENV_DIR}}" "${{PLUGIN_DATA}}/venv.old.$$"
            fi
            mv "${{VENV_TMP}}" "${{VENV_DIR}}"
            # Write the guard only after the swap so a crash mid-build never marks
            # a partial venv as current.
            echo "${{CURRENT_HASH}}" > "${{GUARD}}"
        fi
        trap - EXIT
        rm -rf "${{LOCK}}"
    fi

    # Expose this tool's human-facing CLIs (non -mcp console scripts) on PATH.
    # Cheap and idempotent, so it runs every session start and self-heals.
    mkdir -p "${{HOME}}/.local/bin"
    for _cli in "" {cli_bins}; do
        [ -z "${{_cli}}" ] && continue
        if [ -x "${{VENV_DIR}}/bin/${{_cli}}" ]; then
            ln -sf "${{VENV_DIR}}/bin/${{_cli}}" "${{HOME}}/.local/bin/${{_cli}}"
        fi
    done

    # Install any LaunchAgents shipped with this plugin, only when the venv was
    # just (re)built, to avoid per-session launchctl churn. Plists are templates
    # using __HOME__; runtime scripts land in ~/.local/share/{tool_slug}/.
    _la_src="${{PLUGIN_ROOT}}/launchagents"
    if [ "${{NEEDS_BUILD}}" = "1" ] && [ -d "${{_la_src}}" ]; then
        if [ -d "${{_la_src}}/scripts" ]; then
            mkdir -p "${{HOME}}/.local/share/{tool_slug}"
            for _s in "${{_la_src}}"/scripts/*.sh; do
                [ -e "${{_s}}" ] && install -m 0755 "${{_s}}" "${{HOME}}/.local/share/{tool_slug}/$(basename "${{_s}}")"
            done
        fi
        mkdir -p "${{HOME}}/Library/LaunchAgents"
        for _p in "${{_la_src}}"/*.plist; do
            [ -e "${{_p}}" ] || continue
            _label="$(basename "${{_p}}" .plist)"
            _target="${{HOME}}/Library/LaunchAgents/${{_label}}.plist"
            sed "s#__HOME__#${{HOME}}#g" "${{_p}}" > "${{_target}}"
            # Always unload+load, not skip-if-already-loaded: launchd caches the
            # job definition it was loaded with, so a label already loaded from a
            # prior version would otherwise keep running its stale in-memory
            # ProgramArguments forever, silently diverging from the plist just
            # rendered above -- this already happened for real (issue #146).
            launchctl unload "${{_target}}" 2>/dev/null || true
            launchctl load -w "${{_target}}" 2>/dev/null || true
        done
    fi

    # Write env.example on first install so users know where to put env vars.
    _CONFIG_DIR="${{HOME}}/.config/{tool_slug}"
    mkdir -p "${{_CONFIG_DIR}}"
    if [ ! -f "${{_CONFIG_DIR}}/env" ] && [ ! -f "${{_CONFIG_DIR}}/env.example" ]; then
        cat > "${{_CONFIG_DIR}}/env.example" << 'ENVEOF'
# Plugin env vars for {tool_slug}.
# Copy this file to env and fill in values, then restart Claude.
# Example:
#   OBSIDIAN_VAULT_PATH=/Users/you/Obsidian
#   OBSIDIAN_EXCLUDED_SECTIONS=Work
ENVEOF
    fi
""")

# run_server.sh: self-bootstrapping MCP server launcher.
# Before exec-ing the server it ensures the venv is present AND current (its
# deps.hash matches), rebuilding synchronously otherwise, so it never launches a
# missing or stale venv regardless of hook-vs-server ordering.
# Variables expanded at generation time: {script_name}, {tool_slug}, {deps_hash_cmd}.
_RUN_SERVER_TEMPLATE = textwrap.dedent("""\
    #!/usr/bin/env bash
    # run_server.sh: self-bootstrapping MCP server launcher for {tool_slug}.
    #
    # The SessionStart hook builds the venv, but the harness can launch this
    # server before that hook finishes, or alongside it. Exec-ing whatever venv
    # is present at that moment risks launching a venv left by a prior release --
    # and if that release half-built it, a torn one -- which is what left servers
    # failed after the issue #22 release. So this guards on the same deps.hash the
    # hook writes: if the venv is missing or its hash does not match the current
    # spec, it (re)builds synchronously before exec. install_deps.sh is
    # hash-guarded and safe to run concurrently with the hook, so on the warm
    # path (hash already current) this is just a hash compare and an exec.
    set -euo pipefail

    PLUGIN_ROOT="${{PLUGIN_ROOT:-${{CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")/.." && pwd)}}}}"
    PLUGIN_DATA="${{PLUGIN_DATA:-${{CLAUDE_PLUGIN_DATA:-${{XDG_DATA_HOME:-${{HOME}}/.local/share}}/{tool_slug}}}}}"
    export PLUGIN_ROOT PLUGIN_DATA
    export CLAUDE_PLUGIN_ROOT="${{PLUGIN_ROOT}}" CLAUDE_PLUGIN_DATA="${{PLUGIN_DATA}}"

    VENV_BIN="${{PLUGIN_DATA}}/venv/bin/{script_name}"
    GUARD="${{PLUGIN_DATA}}/deps.hash"
    PYPROJECT="${{PLUGIN_ROOT}}/pyproject.toml"

    # Rebuild if the venv binary is missing, the guard is missing, or the guard
    # does not match the freshly computed hash (a stale venv from a prior spec).
    EXPECTED_HASH=$({deps_hash_cmd})
    if [ ! -x "${{VENV_BIN}}" ] || [ ! -f "${{GUARD}}" ] || [ "$(cat "${{GUARD}}" 2>/dev/null)" != "${{EXPECTED_HASH}}" ]; then
        bash "${{PLUGIN_ROOT}}/hooks/install_deps.sh"
    fi

    # Source user-provided env vars if present (persists across plugin updates).
    _ENV_FILE="${{HOME}}/.config/{tool_slug}/env"
    if [ -f "${{_ENV_FILE}}" ]; then
        set -a
        # shellcheck source=/dev/null
        source "${{_ENV_FILE}}"
        set +a
    fi

    # Exec the server from the now-current venv.
    exec "${{VENV_BIN}}" "$@"
""")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _find_members() -> list[Path]:
    """Return sorted list of package directories under packages/."""
    if not PACKAGES_DIR.exists():
        return []
    return sorted(p for p in PACKAGES_DIR.iterdir() if p.is_dir() and (p / "pyproject.toml").exists())


def _load_pyproject(pkg_dir: Path) -> dict:
    with open(pkg_dir / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def _find_mcp_script(scripts: dict[str, str]) -> tuple[str, str] | None:
    """Return (script_name, entry_point) for the primary MCP script.

    Prefers the script whose entry point ends with 'mcp_server:main'.
    When multiple match, picks the alphabetically first script name so output
    is deterministic. For tools that expose alias scripts (e.g. apple-eventkit-tools
    ships apple-eventkit-mcp, ical-mcp, reminders-mcp), the canonical script
    sorts first alphabetically.
    Returns None when no MCP script is found.
    """
    candidates = [(name, ep) for name, ep in scripts.items() if ep.endswith(_MCP_ENTRY_SUFFIX)]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0]


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


def _tool_slug_from_package(pypi_name: str) -> str:
    """Derive the tool slug from the PyPI name.

    Strips the mrlesmithjr-mcp- prefix. The result is the slug used in
    plugin directories, the MCP server key, and the marketplace entry name.

    Examples:
      mrlesmithjr-mcp-lawnops       -> lawnops
      mrlesmithjr-mcp-weather-tools -> weather-tools
    """
    if pypi_name.startswith(_PYPI_PREFIX):
        return pypi_name[len(_PYPI_PREFIX) :]
    return pypi_name


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------


def _plugin_json(tool_slug: str, version: str, description: str) -> dict:
    """Build the plugin.json payload for a tool.

    The plugin source is the packages/<tool>/ directory itself (cloned by
    'claude plugin install' into ~/.claude/plugins/cache/{id}/{ver}/).
    mcpServers points at .mcp.json in the same directory.

    Hooks are NOT declared here -- they live in hooks/hooks.json which the
    plugin runtime auto-discovers. plugin.json carries only name/version/
    description/author/repository/mcpServers (matching the context-manager
    canonical schema).
    """
    return {
        "name": tool_slug,
        "version": version,
        "description": description,
        "author": {
            "name": "Larry Smith Jr.",
        },
        "repository": "https://github.com/mrlesmithjr/mcp",
        "mcpServers": "./.mcp.json",
    }


def _hooks_json() -> dict:
    """Build the hooks/hooks.json payload for a tool.

    Declares the SessionStart hook that pre-warms the venv. The plugin runtime
    auto-discovers this file from the hooks/ directory -- it must NOT be
    referenced from plugin.json.

    The command is a real shell command string (not a bare path) and includes
    a generous timeout so the first-time venv build can complete.
    """
    return {
        "description": "Install or update the tool venv on session start",
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "bash ${CLAUDE_PLUGIN_ROOT}/hooks/install_deps.sh",
                            "timeout": 300,
                        }
                    ],
                }
            ]
        },
    }


def _mcp_json(server_name: str) -> dict:
    """Build the .mcp.json server definition for a tool.

    The server key sets the 'mcp__<server_name>__' tool namespace in Claude.
    The command delegates to run_server.sh which is self-bootstrapping: it
    builds the venv on first run if the SessionStart hook has not yet fired,
    then execs the server binary. This eliminates the first-run race where
    the MCP server starts before the hook finishes building the venv.
    """
    return {
        "mcpServers": {
            server_name: {
                "command": "bash",
                "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/run_server.sh"],
            }
        }
    }


# Runtime extras to install into a tool's plugin venv beyond the base package.
# These are non-MCP entry points that ship in the same venv (e.g. a daemon run by
# a LaunchAgent). Dev/test extras are intentionally excluded.
#
# An extra here makes a feature *available*, not *running*. ynab-tools carries
# [dashboard] so the ynab-dashboard binary exists in the venv and is on PATH,
# but the plugin ships no dashboard LaunchAgent: starting it is opt-in via
# `ynab dashboard install`, which owns that plist's whole lifecycle.
_RUNTIME_EXTRAS = {
    "ynab-tools": ["dashboard"],  # makes `ynab dashboard install` work; starts nothing
}


def _install_deps_script(tool_slug: str, cli_bins: list[str]) -> str:
    """Render the install_deps.sh venv builder for a tool."""
    extras = _RUNTIME_EXTRAS.get(tool_slug)
    if extras:
        install_target = f'"${{PLUGIN_ROOT}}[{",".join(extras)}]"'
    else:
        install_target = '"${PLUGIN_ROOT}"'
    return _INSTALL_DEPS_TEMPLATE.format(
        tool_slug=tool_slug,
        install_target=install_target,
        cli_bins=" ".join(cli_bins),
        deps_hash_cmd=_DEPS_HASH_CMD,
    )


def _run_server_script(tool_slug: str, script_name: str) -> str:
    """Render the run_server.sh self-bootstrapping launcher for a tool."""
    return _RUN_SERVER_TEMPLATE.format(
        tool_slug=tool_slug,
        script_name=script_name,
        deps_hash_cmd=_DEPS_HASH_CMD,
    )


def _marketplace_entry(tool_slug: str, version: str, description: str) -> dict:
    """Build a single marketplace plugins[] entry.

    source points at packages/<tool>/ so 'claude plugin install' clones the
    Python source directly (no separate plugin bundle needed).
    """
    return {
        "name": tool_slug,
        "version": version,
        "source": f"./packages/{tool_slug}",
        "description": description,
    }


# ---------------------------------------------------------------------------
# Codex plugin manifest generation
# ---------------------------------------------------------------------------

# Curated Codex interface metadata, one entry per marketplace-eligible tool_slug.
# Unlike _RUNTIME_EXTRAS, there is no valid "no metadata" default here: every
# marketplace-eligible tool needs real displayName/category/capabilities/
# default_prompt copy, or Codex ships filler UI text. _codex_interface() raises
# when a tool_slug is missing so a new package without a curated entry fails
# generation loudly, the same severity as any other stale/missing generated file.
#
# category is drawn from the empirically observed set (real cached Codex
# plugins, not a documented enum): Productivity, Developer Tools, Finance,
# Business & Operations, Data & Analytics, Communication, Education & Research,
# Creativity, Travel, Other, Security.
#
# default_prompt: <=3 entries, each <=128 chars, authored from each tool's own
# CLAUDE.md (see _codex_plugin_json() for the enforced length assertion).
_CODEX_INTERFACE: dict[str, dict] = {
    "apple-eventkit-tools": {
        "display_name": "Apple EventKit Tools",
        "category": "Productivity",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["What's on my agenda today across all calendars?"],
    },
    "contacts-tools": {
        "display_name": "Contacts Tools",
        "category": "Productivity",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Find duplicate contacts and merge them."],
    },
    "flightops": {
        "display_name": "FlightOps",
        "category": "Travel",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Search one-way flights from ATL to DEN next month."],
    },
    "homeops": {
        "display_name": "HomeOps",
        "category": "Business & Operations",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Give me the home status dashboard: overdue tasks, alerts, spending."],
    },
    "imessage-tools": {
        "display_name": "iMessage Tools",
        "category": "Communication",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Show my unread messages across all allowed chats."],
    },
    "launchd-tools": {
        "display_name": "LaunchD Tools",
        "category": "Developer Tools",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Give me a one-line health rollup of my LaunchAgents."],
    },
    "lawnops": {
        "display_name": "LawnOps",
        "category": "Business & Operations",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["What's the spray advisory for today based on soil temp and weather?"],
    },
    "mail-tools": {
        "display_name": "Mail Tools",
        "category": "Communication",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Show unread mail counts across all my accounts."],
    },
    "nextdns-tools": {
        "display_name": "NextDNS Tools",
        "category": "Security",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["What got blocked on my network today?"],
    },
    "obsidian-search-tools": {
        "display_name": "Obsidian Search Tools",
        "category": "Productivity",
        "capabilities": ["Interactive", "Read"],
        "default_prompt": ["Search my Obsidian vault for notes about a topic."],
    },
    "sheets-tools": {
        "display_name": "Sheets Tools",
        "category": "Productivity",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["List my spreadsheets and show me one's tabs."],
    },
    "unifi-tools": {
        "display_name": "UniFi Tools",
        "category": "Developer Tools",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Show connected clients and current WAN status."],
    },
    "weather-tools": {
        "display_name": "Weather Tools",
        "category": "Data & Analytics",
        "capabilities": ["Interactive", "Read"],
        "default_prompt": ["How many consecutive days has it rained at my location?"],
    },
    "ynab-tools": {
        "display_name": "YNAB Tools",
        "category": "Finance",
        "capabilities": ["Interactive", "Read", "Write"],
        "default_prompt": ["Give me this month's budget summary and any overspent categories."],
    },
}


def _codex_interface(tool_slug: str) -> dict:
    """Look up curated Codex interface metadata for a tool_slug.

    Raises ValueError (not a silent default) when a marketplace-eligible tool
    has no curated entry -- see the _CODEX_INTERFACE docstring above for why
    that must be a hard generation-time failure.
    """
    try:
        return _CODEX_INTERFACE[tool_slug]
    except KeyError as exc:
        raise ValueError(
            f"No _CODEX_INTERFACE entry for tool_slug={tool_slug!r}. "
            "Add one before this tool can ship a Codex plugin manifest."
        ) from exc


def _codex_plugin_json(tool_slug: str, version: str, description: str, interface: dict) -> dict:
    """Build the .codex-plugin/plugin.json payload for a tool.

    Codex does not expand ${CLAUDE_PLUGIN_ROOT} in stdio argv when it ingests a
    plugin's companion .mcp.json. Use an inline definition with cwd "." instead;
    Codex resolves that cwd to the installed plugin root before launching the
    relative hook path. The Claude manifest and .mcp.json remain unchanged.

    Deliberately excludes repository/license/keywords/homepage and any
    interface asset fields (logo/screenshots/brandColor) -- out of scope per
    the issue, even though the schema permits them.
    """
    default_prompt = interface["default_prompt"]
    if len(default_prompt) > 3:
        raise ValueError(f"{tool_slug}: default_prompt has {len(default_prompt)} entries, max 3")
    for prompt in default_prompt:
        if len(prompt) > 128:
            raise ValueError(f"{tool_slug}: default_prompt entry exceeds 128 chars: {prompt!r}")

    long_description = interface.get("long_description", description)

    return {
        "name": tool_slug,
        "version": version,
        "description": description,
        "author": {
            "name": "Larry Smith Jr.",
        },
        "mcpServers": {
            tool_slug: {
                "command": "bash",
                "args": ["./hooks/run_server.sh"],
                "cwd": ".",
            }
        },
        "interface": {
            "displayName": interface["display_name"],
            "shortDescription": description,
            "longDescription": long_description,
            "developerName": "Larry Smith Jr.",
            "category": interface["category"],
            "capabilities": interface["capabilities"],
            "defaultPrompt": default_prompt,
        },
    }


def _codex_marketplace_entry(tool_slug: str, category: str) -> dict:
    """Build a single Codex marketplace plugins[] entry.

    source.path is relative to the marketplace ROOT directory (confirmed
    empirically against the real codex CLI: 'codex plugin marketplace add
    <repo-root>' resolves source.path relative to <repo-root>, not relative to
    .agents/plugins/), matching the Claude marketplace's ./packages/<tool>
    convention exactly.
    """
    return {
        "name": tool_slug,
        "source": {
            "source": "local",
            "path": f"./packages/{tool_slug}",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": category,
    }


def _codex_marketplace(tools: list[dict]) -> dict:
    """Build the top-level .agents/plugins/marketplace.json payload.

    name MUST be exactly _OWNER["name"] (reused verbatim, not a new/different
    string) -- the issue's acceptance criteria hardcode
    'codex plugin add unifi-tools@mrlesmithjr-mcp'.
    """
    return {
        "name": _OWNER["name"],
        "interface": {
            "displayName": "Larry's MCP Tools",
        },
        "plugins": [
            _codex_marketplace_entry(t["tool_slug"], _codex_interface(t["tool_slug"])["category"]) for t in tools
        ],
    }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _collect_tools() -> list[dict]:
    """Discover all marketplace-eligible tools and return their metadata."""
    tools = []
    for pkg_dir in _find_members():
        data = _load_pyproject(pkg_dir)
        project = data.get("project", {})
        pypi_name = project.get("name", "")
        version = project.get("version", "0.0.0")
        description = project.get("description", "")
        scripts = project.get("scripts", {})

        if pypi_name in _EXCLUDED_PACKAGES:
            continue

        mcp_script = _find_mcp_script(scripts)
        if mcp_script is None:
            # No MCP entry point -- skip (e.g. mcp-common, homeops-coordinator).
            continue

        script_name, _ = mcp_script
        tool_slug = _tool_slug_from_package(pypi_name)
        pkg_dir_resolved = PACKAGES_DIR / pkg_dir.name

        tools.append(
            {
                "pypi_name": pypi_name,
                "tool_slug": tool_slug,
                "script_name": script_name,
                "version": version,
                "description": description,
                "pkg_dir": pkg_dir_resolved,
                # Human-facing CLIs exposed on PATH: every console script that is
                # not the -mcp server entry point.
                "cli_bins": sorted(k for k in scripts if not k.endswith("-mcp")),
            }
        )

    # Sort deterministically by tool slug.
    tools.sort(key=lambda t: t["tool_slug"])
    return tools


def _generate_files(tools: list[dict]) -> dict[Path, str]:
    """Return a mapping of {output_path: content_string} for all generated files."""
    files: dict[Path, str] = {}

    # Per-tool plugin.json, hooks.json, .mcp.json, and hook scripts (inside packages/<tool>/)
    for tool in tools:
        slug = tool["tool_slug"]
        script_name = tool["script_name"]
        pkg_dir: Path = tool["pkg_dir"]

        # .claude-plugin/plugin.json -- NO hooks field; hooks live in hooks/hooks.json
        pj = _plugin_json(slug, tool["version"], tool["description"])
        files[pkg_dir / ".claude-plugin" / "plugin.json"] = json.dumps(pj, indent=2, sort_keys=True) + "\n"

        # hooks/hooks.json -- auto-discovered by the plugin runtime
        hj = _hooks_json()
        files[pkg_dir / "hooks" / "hooks.json"] = json.dumps(hj, indent=2) + "\n"

        # .mcp.json -- delegates to run_server.sh (self-bootstrapping)
        mcp = _mcp_json(slug)
        files[pkg_dir / ".mcp.json"] = json.dumps(mcp, indent=2, sort_keys=True) + "\n"

        # .codex-plugin/plugin.json -- Codex plugin manifest (inline MCP definition)
        codex_interface = _codex_interface(slug)
        cpj = _codex_plugin_json(slug, tool["version"], tool["description"], codex_interface)
        files[pkg_dir / ".codex-plugin" / "plugin.json"] = json.dumps(cpj, indent=2, sort_keys=True) + "\n"

        # hooks/install_deps.sh -- idempotent venv builder (called by hook and run_server.sh)
        files[pkg_dir / "hooks" / "install_deps.sh"] = _install_deps_script(slug, tool["cli_bins"])

        # hooks/run_server.sh -- self-bootstrapping launcher (fixes first-run race)
        files[pkg_dir / "hooks" / "run_server.sh"] = _run_server_script(slug, script_name)

    # Marketplace index
    marketplace = dict(_OWNER)
    marketplace["plugins"] = [_marketplace_entry(t["tool_slug"], t["version"], t["description"]) for t in tools]
    files[MARKETPLACE_FILE] = json.dumps(marketplace, indent=2, sort_keys=True) + "\n"

    # Codex marketplace index
    files[CODEX_MARKETPLACE_FILE] = json.dumps(_codex_marketplace(tools), indent=2, sort_keys=True) + "\n"

    # README plugin-table version column (only when README exists)
    if README_FILE.exists():
        versions = {t["tool_slug"]: t["version"] for t in tools}
        files[README_FILE] = _update_readme_versions(README_FILE.read_text(encoding="utf-8"), versions)

    return files


def _update_readme_versions(original: str, versions: dict[str, str]) -> str:
    """Return README content with each plugin row's version cell set to current.

    Only the version cell is rewritten, and only for rows whose package name is
    a known tool slug. Everything else (name cell, description cell, spacing,
    non-plugin rows, all other lines) is preserved exactly.
    """
    out = []
    for line in original.splitlines(keepends=True):
        m = _README_ROW_RE.match(line.rstrip("\n"))
        if m and m.group(2) in versions:
            newline = f"{m.group(1)}{m.group(2)}{m.group(3)}{versions[m.group(2)]}{m.group(5)}"
            out.append(newline + ("\n" if line.endswith("\n") else ""))
        else:
            out.append(line)
    return "".join(out)


def _write_files(files: dict[Path, str]) -> None:
    """Write all generated files, creating parent directories as needed."""
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _check_files(files: dict[Path, str]) -> list[str]:
    """Return a list of stale file paths (missing or content mismatch)."""
    stale = []
    for path, content in files.items():
        if not path.exists():
            stale.append(str(path.relative_to(REPO_ROOT)))
        elif path.read_text(encoding="utf-8") != content:
            stale.append(str(path.relative_to(REPO_ROOT)))
    return stale


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate marketplace and plugin files from workspace metadata.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if any generated file is missing or stale. Does not write.",
    )
    args = parser.parse_args()

    tools = _collect_tools()
    if not tools:
        print("No marketplace-eligible tools discovered.", file=sys.stderr)

    files = _generate_files(tools)

    if args.check:
        stale = _check_files(files)
        if stale:
            print("Stale or missing generated files:", file=sys.stderr)
            for path in stale:
                print(f"  {path}", file=sys.stderr)
            print("Run: python scripts/gen_marketplace.py", file=sys.stderr)
            sys.exit(1)
        print(f"OK: {len(files)} generated files are up to date.")
        return

    _write_files(files)
    print(f"Generated {len(files)} files for {len(tools)} tool(s):")
    for tool in tools:
        print(f"  {tool['tool_slug']} ({tool['pypi_name']} {tool['version']})")


if __name__ == "__main__":
    main()
