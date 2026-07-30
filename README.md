# mcp

Personal [Model Context Protocol](https://modelcontextprotocol.io/) servers, packaged as a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/) and Claude Code/Codex plugin marketplaces.

Every tool lives under `packages/` as a workspace member with a single shared `uv.lock`. Each tool is also a self-installing plugin: a code generator (`scripts/gen_marketplace.py`) derives both marketplace indexes and the platform-specific plugin files from each package's metadata.

## Packages

Fourteen tools ship as marketplace plugins. Two packages support them and are not plugins.

| Package | Version | What it does |
|---------|---------|--------------|
| `apple-eventkit-tools` | 0.2.4 | Personal calendar via Google Calendar API, Family Calendar and Reminders via native EventKit |
| `contacts-tools` | 0.2.3 | Contacts read/write via the Google People API |
| `flightops` | 0.1.7 | Flight price tracking (Google Flights, no API key) |
| `homeops` | 0.1.24 | Home maintenance operations: tasks, pest, services, HVAC |
| `imessage-tools` | 0.1.9 | Read iMessage chats and send via AppleScript |
| `launchd-tools` | 0.1.4 | Read-only health visibility for personal macOS LaunchAgents, plus safe kickstart |
| `lawnops` | 1.0.45 | Lawn care: weather, irrigation, treatments, products |
| `mail-tools` | 0.1.35 | Apple Mail read, search, compose across accounts |
| `nextdns-tools` | 0.1.13 | NextDNS analytics, blocking stats, device activity |
| `obsidian-search-tools` | 0.1.8 | Hybrid semantic and keyword search over an Obsidian vault |
| `sheets-tools` | 0.2.3 | Google Sheets read/write via the Sheets API v4, plus spreadsheet discovery and sharing via Drive |
| `unifi-tools` | 0.1.27 | UniFi network: devices, clients, VLANs, firewall |
| `weather-tools` | 0.1.6 | Historical weather data via Open-Meteo |
| `ynab-tools` | 1.0.385 | YNAB budget sync, payee cleanup, financial analysis |
| `mcp-common` | (library) | Shared helpers: logging, layered config, paths, errors. Not a plugin. |
| `homeops-coordinator` | (daemon) | LaunchAgent that coordinates home ops across tools. Not a plugin. |

Each package has its own README with setup, tools, and usage. `mcp-common` and `homeops-coordinator` expose no MCP server and are excluded from the marketplace.

## Installing a tool

Each `packages/<tool>/` directory is the plugin source. When you install a plugin, Claude Code clones that directory into its plugin cache. On each session start, the plugin's `hooks/install_deps.sh` builds a persistent virtual environment (surviving plugin updates) and symlinks the tool's CLIs onto your `PATH`. There is no separate `pip install` step.

```bash
# Add this marketplace, then install a tool as a plugin
claude plugin marketplace add mrlesmithjr/mcp
claude plugin install unifi-tools@mrlesmithjr-mcp
```

Tools that need credentials (e.g. `nextdns-tools`, `unifi-tools`, `ynab-tools`) ship a `configure` command or document their config file; see the package README.

Every tool also generates a Codex plugin manifest (`.codex-plugin/plugin.json`) and installs cleanly via `codex plugin marketplace add` / `codex plugin add`. Codex uses an inline, plugin-root-relative MCP launcher (Codex does not expand `${CLAUDE_PLUGIN_ROOT}` in a plugin's stdio argv the way Claude Code does) while Claude Code continues to use its existing `.mcp.json` definition. Verified end to end against a live `codex exec` session (issue #133).

**Allow the tools in Claude Code:** After installing any plugin, its MCP tools are deferred by default -- Claude Code does not pre-load their schemas, so they require an extra lookup step and are unlikely to be used automatically. Add each tool to `permissions.allow` in `~/.claude/settings.json`. The tool namespace follows the pattern `mcp__plugin_<slug>_<slug>__<tool_name>`. Each package README lists the exact strings to add.

## Layout

```
mcp/
├── pyproject.toml                  # Workspace root (virtual, non-distributable)
├── uv.lock                         # Single root lockfile
├── .python-version                 # Python floor (>=3.11)
├── packages/
│   ├── <tool>/                     # One directory per tool; this IS the plugin source
│   │   ├── pyproject.toml          # Package metadata (authoritative)
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json          # GENERATED: Claude plugin metadata
│   │   ├── .codex-plugin/
│   │   │   └── plugin.json          # GENERATED: Codex plugin metadata
│   │   ├── .mcp.json                # GENERATED: Claude Code MCP definition
│   │   └── hooks/
│   │       ├── hooks.json           # GENERATED: SessionStart hook wiring
│   │       ├── install_deps.sh      # GENERATED: builds the persistent venv
│   │       └── run_server.sh        # GENERATED: cold-start launcher
│   ├── mcp-common/                  # Shared library (no plugin files)
│   └── homeops-coordinator/         # Daemon (no plugin files)
├── .claude-plugin/
│   └── marketplace.json             # GENERATED: Claude marketplace index
├── .agents/plugins/
│   └── marketplace.json             # GENERATED: Codex marketplace index
├── scripts/
│   ├── gen_marketplace.py           # Generates all plugin + marketplace files
│   └── install_launchagents.sh      # Manual LaunchAgent (re)install for dev
├── dev/
│   └── register_dev.py              # Registers <tool>-dev MCP servers for local dev
└── docs/
    └── migration.md                 # Per-tool subtree migration runbook
```

The six generated per-package files (the Claude and Codex plugin manifests, the shared MCP definition, and the three hook files) plus both marketplace indexes are produced by `gen_marketplace.py`. Do not hand-edit them. See [CLAUDE.md](CLAUDE.md) for the full plugin-pattern details.

## Developing

```bash
# Sync the workspace (installs all members + dev tools into .venv/)
# --all-packages is required for a virtual (non-package) workspace root
uv sync --all-packages

# Regenerate plugin + marketplace files after any pyproject change
uv run python scripts/gen_marketplace.py

# Verify generated files are up to date (CI check)
uv run python scripts/gen_marketplace.py --check

# Register dev-mode MCP servers (<tool>-dev, pointing at .venv/bin/)
uv run python dev/register_dev.py
```

Run tests per package, not in one combined invocation (test files share names across packages and collide when collected together):

```bash
uv run pytest packages/mcp-common
uv run pytest packages/weather-tools
uv run pytest packages/ynab-tools    # requires: uv sync --all-packages --extra dashboard
```

## Adding a tool

Existing tools were migrated in from standalone repos via `git subtree`. To add another, follow the per-tool runbook in [docs/migration.md](docs/migration.md).

## Shared library

`mcp-common` (`mrlesmithjr-mcp-common`) is a dependency of every tool and is published separately to PyPI so plugins can resolve it when installed outside the workspace. See [CLAUDE.md](CLAUDE.md#mcp-common-pypi-publishing) for the publish flow.
