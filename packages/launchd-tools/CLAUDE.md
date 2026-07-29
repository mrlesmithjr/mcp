# CLAUDE.md

## Project Overview

Read-only health visibility for personal macOS LaunchAgents, plus safe kickstart.

MCP server built with FastMCP. Entry point: `launchd-tools-mcp`.

**4 MCP tools**: `agent_status`, `agent_health`, `agent_logs`, `agent_kickstart`

## Key Commands

```bash
# Run health check
launchd-tools health

# List all agents with detail
launchd-tools status

# Tail logs for an agent
launchd-tools logs --label com.mrlesmithjr.context-manager

# Kickstart an agent
launchd-tools kickstart --label com.homeops.task-escalation

# Run tests
uv run pytest packages/launchd-tools

# Lint and format
uv run ruff check packages/launchd-tools
uv run ruff format packages/launchd-tools
```

## Architecture

```
launchd_tools/
├── __init__.py       # Package init
├── __main__.py       # python -m launchd_tools (runs MCP server)
├── config.py         # LABEL_PREFIXES loader (layered, never raises)
├── launchctl.py      # All subprocess calls + pure parsers
├── health.py         # Pure classification: classify_kind, is_healthy
├── mcp_server.py     # FastMCP server with 4 tools
└── cli.py            # argparse CLI mirror (status/health/logs/kickstart)
tests/
├── fixtures/         # Captured launchctl output for offline testing
│   ├── launchctl_list.txt
│   ├── print_context_manager.txt
│   ├── print_forgejo_backup.txt
│   ├── print_utility_anomaly.txt
│   └── print_ynab_dashboard.txt
└── test_mcp_server.py
```

## Config (Optional)

Layered config, never raises. Resolution order (highest wins):
1. `LAUNCHD_TOOLS_LABEL_PREFIXES` env var (comma-separated)
2. `~/.config/launchd_tools/config.yaml` (label_prefixes list)
3. Built-in defaults: com.homeops, com.lawnops, com.ynab-tools, com.mrlesmithjr, com.methodicalcloud, com.larrysmithjr

See `config.yaml.example` for the config file format.

## Key Patterns

- All subprocess calls: `args=list, shell=False, capture_output=True`
- Parser (`parse_print`) is pure (no I/O), tested against captured fixtures
- `agent_kickstart` validates charset `^[A-Za-z0-9._-]+$` then allowlist before any subprocess call
- `com.ynab-tools.dashboard` is a KeepAlive daemon: classified as `daemon`, healthy = running+pid (not exit code)
- `(never exited)` sentinel parses to `None` last_exit_code, which counts as healthy for scheduled jobs

## MCP Tools

| Tool | Purpose |
|------|---------|
| `agent_status` | Full list with per-agent kind/health/state/runs/last_run |
| `agent_health` | Briefing rollup: "All N healthy." or "K failed: label (exit N), ..." |
| `agent_logs` | Tail stdout log for one label (50 lines default) |
| `agent_kickstart` | Safe restart with allowlist + charset guard |

## Adding Tools

Add `@mcp.tool()` functions to `mcp_server.py`. All tools must:
- Return `str` (JSON via `json.dumps`)
- Wrap body in `try/except Exception as e` returning `json.dumps({"error": str(e)})`
- Use `X | None` union syntax (not `Optional[X]`)
- Declare `annotations=ToolAnnotations(...)`: read tools use the shared `_READ_ONLY`
  (`readOnlyHint=True`); a state-changing tool sets `readOnlyHint=False` and
  `destructiveHint=True` when its effect is not easily undone (see `agent_kickstart`).
  This is the workspace annotation convention; the root `CLAUDE.md` holds the full table.
