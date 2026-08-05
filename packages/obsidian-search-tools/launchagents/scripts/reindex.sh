#!/usr/bin/env bash
# Rebuild the obsidian-search-tools vault index.
# Sources ~/.config/obsidian-search-tools/env for OBSIDIAN_VAULT_PATH.
set -euo pipefail

ENV_FILE="${HOME}/.config/obsidian-search-tools/env"
if [ -f "${ENV_FILE}" ]; then
    # set -a exports all variables defined in the sourced file so child
    # processes (the obsidian-search-tools subprocess) inherit them.
    set -a
    # shellcheck source=/dev/null
    . "${ENV_FILE}"
    set +a
fi

if [ -z "${OBSIDIAN_VAULT_PATH:-}" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: OBSIDIAN_VAULT_PATH not set. Edit ~/.config/obsidian-search-tools/env"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting vault reindex (vault: ${OBSIDIAN_VAULT_PATH})..."
# --skip-if-fresh: RunAtLoad (login) and a StartCalendarInterval fire can land
# close together (e.g. login right after a scheduled 06:00 run); this guard
# skips the rebuild if the index is younger than OBSIDIAN_REINDEX_STALENESS_HOURS
# (default 2h) so the ~130MB embedding model isn't loaded twice back-to-back.
"${HOME}/.local/bin/obsidian-search-tools" reindex --skip-if-fresh
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Reindex complete."
