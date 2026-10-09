"""Start the MCP server over stdio: ``uv run python -m legacybridge.mcp_server``.

When ``LB_TRACING`` is on, this process exports its own spans; the MCP SDK parents them under the
client's tool-call span through the W3C trace context it receives (ADR-007).
"""

from legacybridge.env import load_env
from legacybridge.mcp_server.server import build_server
from legacybridge.observability.tracing import setup_tracing, shutdown_tracing

if __name__ == "__main__":
    load_env()
    setup_tracing("legacybridge-mcp")
    try:
        build_server().run("stdio")
    finally:
        shutdown_tracing()
