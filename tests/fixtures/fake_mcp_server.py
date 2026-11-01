"""Minimal stdio MCP server used by client integration tests."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("aiji-test-server")


@server.tool()
def echo(text: str) -> str:
    """Return a prefixed copy of the supplied text."""
    return f"echo:{text}"


if __name__ == "__main__":
    server.run("stdio")
