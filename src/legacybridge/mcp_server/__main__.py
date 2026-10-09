"""Start the MCP server over stdio: ``uv run python -m legacybridge.mcp_server``."""

from legacybridge.mcp_server.server import build_server

if __name__ == "__main__":
    build_server().run("stdio")
