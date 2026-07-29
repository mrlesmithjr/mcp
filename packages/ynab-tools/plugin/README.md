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

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ynab-tools": {
      "command": "ynab-mcp"
    }
  }
}
```

`ynab-mcp` is the console script installed with the package, so no working directory or module path is needed once it is on your PATH.

### Prerequisites

1. `ynab-tools` installed: `uv tool install --editable .` from the `packages/ynab-tools` directory of your repo clone (or `pip install -e .`)
2. YNAB credentials configured via `ynab configure`, which writes `~/.config/ynab-tools/config.json` (a project-root `.env` also works as a dev fallback)
