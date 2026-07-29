# launchd-tools

Read-only health visibility for personal macOS LaunchAgents, plus safe kickstart

## Setup

1. Copy `config.yaml.example` to `config.yaml` and fill in your values.
2. Install the tool:


```bash
uv tool install --editable .
```

## MCP Server

Register with Claude Code:

```bash
claude mcp add -s user launchd-tools-mcp launchd-tools-mcp
```

Or add to Claude Desktop (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "launchd-tools": {
      "command": "launchd-tools-mcp"
    }
  }
}
```

## CLI

```bash
launchd-tools --help
```


## License

MIT
