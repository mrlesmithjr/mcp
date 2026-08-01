#!/usr/bin/env bash
# Install the personal LaunchAgents for the mrlesmithjr/mcp scheduled jobs.
# Idempotent: re-running re-syncs the runtime scripts, re-renders the
# plists, and reloads each agent.
#
# Plists are committed as templates using the literal __HOME__ placeholder so
# the repo stays free of absolute user paths. This script substitutes $HOME at
# install time (launchd does not expand ~ or variables in ProgramArguments).
#
# Only ship a launchagents/ dir under a package for an unattended scheduled
# job (see mcp/CLAUDE.md) -- a persistent user-facing service must not be
# plugin-installed. Direct-binary agents run from the plugin venv at
# ${CLAUDE_PLUGIN_DATA}/venv, which persists across plugin updates, and need
# no runtime script at all -- their plists call the venv binary + subcommand
# directly. Any script-driven agents call the tool's CLI via
# ~/.local/bin/<tool> and need their runtime .sh copied into
# ~/.local/share/<tool>/. A package with no script-driven agents has no
# launchagents/scripts/ dir (or an empty one); install_scripts() below must
# tolerate that.
#
# Packages are discovered dynamically by scanning packages/*/launchagents/ --
# do not hardcode package names here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LA_DIR"

LOADED_COUNT=0

# Copy a package's runtime scripts into the tool data dir referenced by its plists.
install_scripts() {
  local pkg="$1"
  local src="$REPO_ROOT/packages/$pkg/launchagents/scripts"
  [[ -d "$src" ]] || return 0
  local dst="$HOME/.local/share/$pkg"
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
  LOADED_COUNT=$((LOADED_COUNT + 1))
}

for pkg_dir in "$REPO_ROOT"/packages/*/launchagents; do
  [[ -d "$pkg_dir" ]] || continue
  pkg="$(basename "$(dirname "$pkg_dir")")"
  install_scripts "$pkg"
  for plist in "$pkg_dir"/*.plist; do
    [[ -e "$plist" ]] || continue
    render_and_load "$plist"
  done
done

echo "Loaded ${LOADED_COUNT} personal agent(s)."
