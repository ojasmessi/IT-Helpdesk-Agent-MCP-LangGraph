"""
STEP 2 — Talk to the MCP server WITHOUT any AI (over SSE)
=========================================================

Before adding an LLM, prove the plumbing works. This script:
  1. connects to the already-running MCP server over SSE,
  2. lists the tools it offers,
  3. calls two tools directly.

If this works, any problem later is in the agent — not the server.

Run (two terminals):
  Terminal 1:  python mcp_server/helpdesk_server.py
  Terminal 2:  python steps/step2_test_mcp_client.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so we can import mcp_config
from mcp_config import HELPDESK_URL, TRANSPORT, load_tools  # noqa: E402


async def main():
    # load_tools() builds a MultiServerMCPClient from mcp_config.mcp_servers():
    #   {"helpdesk": {"transport": "sse", "url": "http://127.0.0.1:8001/sse"}}
    # then asks the server "what tools do you have?" and converts each MCP tool
    # into a LangChain Tool object the agent can use.
    tools = await load_tools()
    print(f"Connected to {HELPDESK_URL} ({TRANSPORT}). The server offers {len(tools)} tools:\n")
    for t in tools:
        print(f"  - {t.name}: {t.description.splitlines()[0]}")

    by_name = {t.name: t for t in tools}

    def text(result):
        # MCP tools return a list of "content blocks"; we just want the text.
        if isinstance(result, list):
            return "\n".join(b.get("text", "") for b in result if isinstance(b, dict))
        return str(result)

    print("\n--- Calling search_knowledge_base('vpn not connecting') ---")
    print(text(await by_name["search_knowledge_base"].ainvoke({"query": "vpn not connecting"})))

    print("\n--- Calling check_service_status('vpn') ---")
    print(text(await by_name["check_service_status"].ainvoke({"service": "vpn"})))


if __name__ == "__main__":
    asyncio.run(main())
