#!/usr/bin/env bash
# Install the personal LaunchAgents for the mrlesmithjr/mcp daemons and
# scheduled jobs. Idempotent: re-running re-syncs the runtime scripts,
# re-renders the plists, and reloads each agent.
#
# Plists are committed as templates using the literal __HOME__ placeholder so
# the repo stays free of absolute user paths. This script substitutes $HOME at
# install time (launchd does not expand ~ or variables in ProgramArguments).
#
# Direct-binary agents run from the plugin venv at ${CLAUDE_PLUGIN_DATA}/venv,
# which persists across plugin updates, and need no runtime script at all --
# their plists call the venv binary + subcommand directly. Any remaining
# script-driven agents call `claude -p` headlessly and need their runtime .sh
# copied into ~/.local/share/<tool>/. A package with no script-driven agents
# left has no launchagents/scripts/ dir (or an empty one); install_scripts()
# below must tolerate that.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LA_DIR"

# Copy a package's runtime scripts into the tool data dir referenced by its plists.
install_scripts() {
  local pkg="$1" tool="$2"
  local src="$REPO_ROOT/packages/$pkg/launchagents/scripts"
  [[ -d "$src" ]] || return 0
  local dst="$HOME/.local/share/$tool"
  mkdir -p "$dst"
  local s
  for s in "$src"/*.sh; do
    [[ -e "$s" ]] || continue
    install -m 0755 "$s" "$dst/$(basename "$s")"
  done
}

render_and_load() {
  local plist="$1"
  local label target
  label="$(basename "$plist" .plist)"
  target="$LA_DIR/$label.plist"
  sed "s#__HOME__#$HOME#g" "$plist" > "$target"
  plutil -lint "$target" >/dev/null
  launchctl unload -w "$target" 2>/dev/null || true
  launchctl load -w "$target"
  echo "loaded $label"
}

install_scripts homeops homeops
install_scripts lawnops lawnops

# Warn (do not fail) if a venv binary is missing: the plugin is not installed yet
# or its venv has not built. The agent loads but its first run will no-op.
for vb in \
  "$HOME/.claude/plugins/data/homeops-mrlesmithjr-mcp/venv/bin/homeops" \
  "$HOME/.claude/plugins/data/lawnops-mrlesmithjr-mcp/venv/bin/lawnops" \
  "$HOME/.claude/plugins/data/ynab-tools-mrlesmithjr-mcp/venv/bin/ynab-dashboard"; do
  [[ -x "$vb" ]] || echo "WARN: missing $vb (install the plugin and let its venv build first)"
done

for plist in \
  "$REPO_ROOT"/packages/homeops/launchagents/*.plist \
  "$REPO_ROOT"/packages/lawnops/launchagents/*.plist \
  "$REPO_ROOT"/packages/ynab-tools/launchagents/*.plist; do
  render_and_load "$plist"
done

echo "Loaded $(launchctl list | grep -cE 'com\.(homeops|lawnops|ynab-tools)\.') personal agents."
