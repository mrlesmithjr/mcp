"""FastMCP server factory and runner helpers.

Each tool's main() becomes a two-liner:

    from mcp_common.server import build_server, run_stdio
    mcp = build_server("my-tool", instructions="...")

    @mcp.tool()
    def do_thing(...) -> str: ...

    def main() -> None:
        run_stdio(mcp)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def build_server(name: str, instructions: str | None = None) -> FastMCP:
    """Create and return a configured FastMCP instance.

    Parameters
    ----------
    name:
        Human-readable server name shown in Claude Code's MCP panel.
    instructions:
        Optional guidance string injected into the system prompt to tell
        Claude how to use this server's tools.
    """
    kwargs: dict = {"name": name}
    if instructions is not None:
        kwargs["instructions"] = instructions
    return FastMCP(**kwargs)


def run_stdio(mcp: FastMCP) -> None:
    """Run the MCP server over stdio transport.

    Call from each tool's main() entry point. Blocks until the server exits.
    """
    mcp.run(transport="stdio")
