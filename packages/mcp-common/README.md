# mrlesmithjr-mcp-common

Shared building blocks for [FastMCP](https://github.com/jlowin/fastmcp) servers: stderr-safe logging, layered configuration, XDG path resolution, and a consistent JSON error envelope. It is the common foundation under the [mrlesmithjr/mcp](https://github.com/mrlesmithjr/mcp) tool suite, but has no dependency on those tools and is usable in any MCP server.

Import as `mcp_common`.

## Install

```bash
pip install mrlesmithjr-mcp-common
# or
uv add mrlesmithjr-mcp-common
```

## Usage

```python
import json
from mcp_common.server import build_server, run_stdio
from mcp_common.errors import safe_tool

mcp = build_server("my-tool", instructions="What this server does.")

@mcp.tool()
@safe_tool
def do_thing(x: str) -> str:
    return json.dumps({"status": "ok", "data": x})

def main() -> None:
    run_stdio(mcp)
```

`safe_tool` wraps a tool so unhandled exceptions return a structured `{"error": ...}` envelope instead of crashing the server. `build_server` and `run_stdio` handle FastMCP setup and stdio transport; `configure_logging()` forces logs to stderr (stdout is reserved for the protocol).

## Modules

| Module | Purpose |
|--------|---------|
| `mcp_common.logging` | `configure_logging()` forcing stderr output (required for stdio transport) |
| `mcp_common.server` | `build_server(name, instructions)` and `run_stdio(mcp)` |
| `mcp_common.config` | `load_layered_config(tool_name, env_map)`: env vars > config.json > .env fallback |
| `mcp_common.paths` | `config_dir()`, `config_file()`, `data_dir()` XDG path resolvers |
| `mcp_common.errors` | `tool_error()`, `error_json()`, `safe_tool` decorator |
| `mcp_common.coordinator_events` | `emit_coordinator_event(source, event_type, payload) -> bool`: self-healing write onto homeops-coordinator's `events.db` bus. Never raises; returns `False` on failure so callers can fire-and-forget it after a state change. |
| `mcp_common.google_oauth` | `GoogleOAuthClient`: shared Google OAuth2 base class (token storage/refresh, authenticated `request()`) for any tool calling a Google REST API. Credentials at `~/.config/google/credentials.json` (shared across tools); tokens per-tool at `~/.config/<tool_name>/<token_filename>`. Used by apple-eventkit-tools' `GoogleCalendarClient` (issue #59) and contacts-tools' `GooglePeopleClient` (issue #60). |

## Requirements

- Python 3.11+

## License

MIT. Part of the [mrlesmithjr/mcp](https://github.com/mrlesmithjr/mcp) workspace.
