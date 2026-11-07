"""
Shared MCP connection settings — the ONE place that says how to reach MCP servers.

Every client (agent/graph.py, steps/step2, steps/step3, Chainlit) imports
mcp_servers() from here, so switching transport or adding a server is a
one-file change.

Transport: SSE (Server-Sent Events) over HTTP
---------------------------------------------
With stdio, each client launched its own private copy of the server as a
subprocess. With SSE the server is a standalone web service:

    client  --GET  /sse        -->  server   (long-lived stream: server -> client messages)
    client  --POST /messages/  -->  server   (client -> server requests)

One running server can now serve many agents/UIs at once, and can live on
another machine or in a container.

Settings come from .env (defaults shown):
    MCP_TRANSPORT=sse                 # or streamable_http (newer MCP transport)
    MCP_HOST=127.0.0.1
    MCP_PORT=8001                     # 8000 is left free for Chainlit
    MCP_URL=                          # optional full URL; overrides host/port
"""

import os

from dotenv import load_dotenv

load_dotenv()

TRANSPORT = os.getenv("MCP_TRANSPORT", "sse").strip().lower().replace("-", "_")
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8001"))

if TRANSPORT not in {"sse", "streamable_http"}:
    raise ValueError(f"MCP_TRANSPORT must be 'sse' or 'streamable_http', got '{TRANSPORT}'")

_DEFAULT_PATH = "/sse" if TRANSPORT == "sse" else "/mcp"
HELPDESK_URL = os.getenv("MCP_URL") or f"http://{HOST}:{PORT}{_DEFAULT_PATH}"


def mcp_servers() -> dict:
    """Connection config for MultiServerMCPClient.
    To add another MCP server, add another entry with its own URL."""
    return {
        "helpdesk": {
            "transport": TRANSPORT,
            "url": HELPDESK_URL,
            # Production: add auth, e.g. "headers": {"Authorization": f"Bearer {os.getenv('MCP_TOKEN')}"}
        },
    }


async def load_tools():
    """Connect to every MCP server and return its tools as LangChain tools.
    Gives a clear message instead of a long traceback if a server isn't running."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(mcp_servers())
    try:
        return await client.get_tools()
    except Exception as exc:  # connection refused etc. arrive wrapped in ExceptionGroups
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]          # unwrap to the real cause
        raise SystemExit(
            f"\n❌ Could not connect to the MCP server at {HELPDESK_URL} ({TRANSPORT}).\n"
            "   Start it first in another terminal:\n"
            "       python mcp_server/helpdesk_server.py\n"
            f"   (details: {type(exc).__name__}: {exc})\n"
        ) from None
