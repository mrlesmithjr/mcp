#!/usr/bin/env bash
# Post-process a rendered reindex plist: fill in the StartCalendarInterval
# block from the user's configured cadence (OBSIDIAN_REINDEX_TIMES),
# persisted at ~/.config/obsidian-search-tools/env. Falls back to the
# packaged default (3x/day) when unset.
#
# Invoked by the generated hooks/install_deps.sh (scripts/gen_marketplace.py)
# after it substitutes __HOME__ into $1, and by scripts/install_launchagents.sh
# for the dev-checkout re-install path. $1 is the already-__HOME__-rendered
# plist path; edits happen in place.
set -euo pipefail

TARGET="${1:?usage: render.sh <rendered-plist-path>}"

ENV_FILE="${HOME}/.config/obsidian-search-tools/env"
if [ -f "${ENV_FILE}" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${ENV_FILE}"
    set +a
fi

CLI="${HOME}/.local/bin/obsidian-search-tools"
if [ -x "${CLI}" ]; then
    "${CLI}" schedule render "${TARGET}"
else
    echo "[render.sh] WARNING: ${CLI} not found, skipping schedule render for ${TARGET}" >&2
fi
