#!/usr/bin/env bash
# run_server.sh: self-bootstrapping MCP server launcher for imessage-tools.
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

PLUGIN_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}}"
PLUGIN_DATA="${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-${HOME}/.local/share}/imessage-tools}}"
export PLUGIN_ROOT PLUGIN_DATA
export CLAUDE_PLUGIN_ROOT="${PLUGIN_ROOT}" CLAUDE_PLUGIN_DATA="${PLUGIN_DATA}"

VENV_BIN="${PLUGIN_DATA}/venv/bin/imessage-mcp"
GUARD="${PLUGIN_DATA}/deps.hash"
PYPROJECT="${PLUGIN_ROOT}/pyproject.toml"

# Rebuild if the venv binary is missing, the guard is missing, or the guard
# does not match the freshly computed hash (a stale venv from a prior spec).
EXPECTED_HASH=$(cat "${PYPROJECT}" "${PLUGIN_ROOT}/hooks/install_deps.sh" | shasum -a 256 | awk '{print $1}')
if [ ! -x "${VENV_BIN}" ] || [ ! -f "${GUARD}" ] || [ "$(cat "${GUARD}" 2>/dev/null)" != "${EXPECTED_HASH}" ]; then
    bash "${PLUGIN_ROOT}/hooks/install_deps.sh"
fi

# Source user-provided env vars if present (persists across plugin updates).
_ENV_FILE="${HOME}/.config/imessage-tools/env"
if [ -f "${_ENV_FILE}" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${_ENV_FILE}"
    set +a
fi

# Exec the server from the now-current venv.
exec "${VENV_BIN}" "$@"
