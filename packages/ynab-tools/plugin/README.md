# ynab-tools Plugin

YNAB budget management plugin for Claude Cowork. Provides budget workflow knowledge and slash commands that work with the ynab-tools MCP server.

## Components

### Skill: ynab-workflow

Loads budget methodology and workflow knowledge so Claude understands how to use the YNAB tools effectively - income structure, two funding streams, category protection tiers, common workflows.

### Commands

| Command | Description |
|---------|-------------|
| `/budget` | Quick budget health check - RTA, overspent, underfunded, planned expenses |
| `/spending` | Monthly spending vs budget with recommendations |
| `/sync` | Refresh data from YNAB API |

## MCP Server Setup

The ynab-tools MCP server must be configured in Claude Desktop's native config (not in this plugin) because Cowork runs in a sandbox that cannot spawn local processes.

Merge into `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS).
That file also holds Desktop's own settings, so add the key rather than replacing it:

```json
{
  "mcpServers": {
    "ynab-tools": {
      "command": "/absolute/path/to/ynab-mcp"
    }
  }
}
```

The path must be absolute. Desktop launches servers with a minimal environment and
does not inherit your shell `PATH`, so a bare `ynab-mcp` fails to start with no
useful error. Find yours with `which ynab-mcp`. Then quit Desktop with **⌘Q** and
reopen — closing the window does not reload the config.

Full setup, verification, and troubleshooting: [../docs/mcp-server.md](../docs/mcp-server.md).

### Prerequisites

1. `ynab-tools` installed: `uv tool install --editable .` from the `packages/ynab-tools` directory of your repo clone (or `pip install -e .`)
2. YNAB credentials configured via `ynab configure`, which writes `~/.config/ynab-tools/config.json` (a project-root `.env` also works as a dev fallback)
