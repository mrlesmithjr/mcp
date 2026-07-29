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

    PLUGIN_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}}"
    PLUGIN_DATA="${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-${HOME}/.local/share}/mail-tools}}"
    export PLUGIN_ROOT PLUGIN_DATA
    export CLAUDE_PLUGIN_ROOT="${PLUGIN_ROOT}" CLAUDE_PLUGIN_DATA="${PLUGIN_DATA}"

    VENV_DIR="${PLUGIN_DATA}/venv"
    GUARD="${PLUGIN_DATA}/deps.hash"
    PYPROJECT="${PLUGIN_ROOT}/pyproject.toml"

    # Codex does not pre-create PLUGIN_DATA the way Claude Code's plugin runtime
    # does, so the build.lock mkdir below can fail with a missing parent dir.
    mkdir -p "${PLUGIN_DATA}"

    # Hash the dependency spec AND this installer (so a change to the install
    # target, e.g. adding a runtime extra, invalidates the guard and rebuilds).
    CURRENT_HASH=$(cat "${PYPROJECT}" "${PLUGIN_ROOT}/hooks/install_deps.sh" | shasum -a 256 | awk '{print $1}')

    # Rebuild the venv only when the dependency spec or this installer changes.
    NEEDS_BUILD=1
    if [ -f "${GUARD}" ] && [ "$(cat "${GUARD}")" = "${CURRENT_HASH}" ]; then
        NEEDS_BUILD=0
    fi

    if [ "${NEEDS_BUILD}" = "1" ]; then
        # Serialize rebuilds with a mkdir lock. The SessionStart hook and
        # run_server.sh can both decide to rebuild at the same session start (the
        # latter now rebuilds a stale venv before exec). Without serialization
        # their atomic swaps collide: two `mv venv.tmp venv` race and the second,
        # finding venv/ already present, nests its tree *inside* the first. mkdir
        # is atomic, so exactly one builder proceeds; the other waits, re-checks
        # the hash, and finds the venv already current. A lock held by a dead PID
        # (a builder that was killed) is stolen; a live lock is waited on, with a
        # ~2min cap before forcing past a wedged holder.
        LOCK="${PLUGIN_DATA}/build.lock"
        _waited=0
        while ! mkdir "${LOCK}" 2>/dev/null; do
            _lpid="$(cat "${LOCK}/pid" 2>/dev/null || true)"
            if [ -n "${_lpid}" ] && ! kill -0 "${_lpid}" 2>/dev/null; then
                rm -rf "${LOCK}" 2>/dev/null || true
                continue
            fi
            _waited=$((_waited + 1))
            if [ "${_waited}" -gt 400 ]; then
                # Wedged holder: force past it, then reset and fall through to the
                # sleep so a holder that immediately re-creates the lock cannot
                # spin us in a tight rm/mkdir loop.
                rm -rf "${LOCK}" 2>/dev/null || true
                _waited=0
            fi
            sleep 0.3
        done
        echo "$$" > "${LOCK}/pid"
        # From here on, always release the lock (and drop any in-progress temp
        # dir) on exit, so a crash mid-build leaves the existing venv and guard
        # untouched and does not wedge the lock.
        trap 'rm -rf "${VENV_TMP:-}" "${LOCK}"' EXIT

        # Re-check under the lock: a builder we waited behind may have already
        # made the venv current, in which case there is nothing to build.
        if [ -f "${GUARD}" ] && [ "$(cat "${GUARD}")" = "${CURRENT_HASH}" ]; then
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
            VENV_TMP="${PLUGIN_DATA}/venv.tmp.$$"
            # Reclaim leftovers: an interrupted build's temp dir, and superseded
            # venvs from a prior session. The dir suffix is the PID of the builder
            # that created it; skip any whose process is still alive (defense in
            # depth -- the lock already excludes a concurrent builder).
            for _leftover in "${PLUGIN_DATA}"/venv.tmp.* "${PLUGIN_DATA}"/venv.old.*; do
                [ -e "${_leftover}" ] || continue
                _opid="${_leftover##*.}"
                case "${_opid}" in
                    ''|*[!0-9]*) ;;
                    *) if kill -0 "${_opid}" 2>/dev/null; then continue; fi ;;
                esac
                rm -rf "${_leftover}"
            done
            uv venv --relocatable --python python3 "${VENV_TMP}"
            uv pip install --python "${VENV_TMP}/bin/python" "${PLUGIN_ROOT}"
            # Atomic swap: rename the current venv aside, then rename the freshly
            # built one into place. Each mv is a single atomic rename. The
            # superseded venv is NOT deleted here: a server already launched from
            # it this session may still be importing, and removing it underneath
            # would break those imports. It is reclaimed by the leftover-clear
            # above on the next rebuild, after that session has ended.
            if [ -d "${VENV_DIR}" ]; then
                mv "${VENV_DIR}" "${PLUGIN_DATA}/venv.old.$$"
            fi
            mv "${VENV_TMP}" "${VENV_DIR}"
            # Write the guard only after the swap so a crash mid-build never marks
            # a partial venv as current.
            echo "${CURRENT_HASH}" > "${GUARD}"
        fi
        trap - EXIT
        rm -rf "${LOCK}"
    fi

    # Expose this tool's human-facing CLIs (non -mcp console scripts) on PATH.
    # Cheap and idempotent, so it runs every session start and self-heals.
    mkdir -p "${HOME}/.local/bin"
    for _cli in "" ; do
        [ -z "${_cli}" ] && continue
        if [ -x "${VENV_DIR}/bin/${_cli}" ]; then
            ln -sf "${VENV_DIR}/bin/${_cli}" "${HOME}/.local/bin/${_cli}"
        fi
    done

    # Install any LaunchAgents shipped with this plugin, only when the venv was
    # just (re)built, to avoid per-session launchctl churn. Plists are templates
    # using __HOME__; runtime scripts land in ~/.local/share/mail-tools/.
    _la_src="${PLUGIN_ROOT}/launchagents"
    if [ "${NEEDS_BUILD}" = "1" ] && [ -d "${_la_src}" ]; then
        if [ -d "${_la_src}/scripts" ]; then
            mkdir -p "${HOME}/.local/share/mail-tools"
            for _s in "${_la_src}"/scripts/*.sh; do
                [ -e "${_s}" ] && install -m 0755 "${_s}" "${HOME}/.local/share/mail-tools/$(basename "${_s}")"
            done
        fi
        mkdir -p "${HOME}/Library/LaunchAgents"
        for _p in "${_la_src}"/*.plist; do
            [ -e "${_p}" ] || continue
            _label="$(basename "${_p}" .plist)"
            _target="${HOME}/Library/LaunchAgents/${_label}.plist"
            sed "s#__HOME__#${HOME}#g" "${_p}" > "${_target}"
            # Always unload+load, not skip-if-already-loaded: launchd caches the
            # job definition it was loaded with, so a label already loaded from a
            # prior version would otherwise keep running its stale in-memory
            # ProgramArguments forever, silently diverging from the plist just
            # rendered above -- this already happened for real (issue #146).
            launchctl unload "${_target}" 2>/dev/null || true
            launchctl load -w "${_target}" 2>/dev/null || true
        done
    fi

    # Write env.example on first install so users know where to put env vars.
    _CONFIG_DIR="${HOME}/.config/mail-tools"
    mkdir -p "${_CONFIG_DIR}"
    if [ ! -f "${_CONFIG_DIR}/env" ] && [ ! -f "${_CONFIG_DIR}/env.example" ]; then
        cat > "${_CONFIG_DIR}/env.example" << 'ENVEOF'
# Plugin env vars for mail-tools.
# Copy this file to env and fill in values, then restart Claude.
# Example:
#   OBSIDIAN_VAULT_PATH=/Users/you/Obsidian
#   OBSIDIAN_EXCLUDED_SECTIONS=Work
ENVEOF
    fi
