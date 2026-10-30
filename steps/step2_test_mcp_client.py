"""
STEP 2 — Talk to the MCP server WITHOUT any AI
===============================================

Before adding an LLM, prove the plumbing works. This script:
  1. starts the MCP server as a subprocess (stdio transport),
  2. lists the tools it offers,
  3. calls two tools directly.

If this works, any problem later is in the agent — not the server.

Run:  python steps/step2_test_mcp_client.py
"""

import asyncio
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

SERVER = Path(__file__).resolve().parent.parent / "mcp_server" / "helpdesk_server.py"


async def main():
    # MultiServerMCPClient can connect to MANY servers at once — one dict entry each.
    client = MultiServerMCPClient({
        "helpdesk": {
            "transport": "stdio",
            "command": sys.executable,       # the same Python you are running now
            "args": [str(SERVER)],
        }
    })

    # get_tools() connects, asks the server "what tools do you have?" and converts
    # each MCP tool into a LangChain Tool object the agent can use.
    tools = await client.get_tools()
    print(f"Connected. The server offers {len(tools)} tools:\n")
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
