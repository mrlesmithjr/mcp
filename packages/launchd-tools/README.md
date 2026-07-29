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

Or add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "launchd-tools": {
      "command": "/absolute/path/to/launchd-tools-mcp"
    }
  }
}
```

The path must be absolute — Claude Desktop does not inherit your shell `PATH`, so a
bare command name fails to start with no useful error. Find yours with
`which launchd-tools-mcp`, then quit Desktop with **⌘Q** and reopen.

## CLI

```bash
launchd-tools --help
```


## License

MIT
