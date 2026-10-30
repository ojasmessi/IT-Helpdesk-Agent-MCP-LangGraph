"""
STEP 3 — Your first agent (the quick, prebuilt way)
====================================================

LangChain's create_agent() builds a ready-made LangGraph "ReAct" agent:
    LLM thinks -> picks a tool -> tool runs -> LLM reads result -> repeat -> answer

Here we give it the MCP tools from Step 2. The LLM never sees Python code —
it only sees each tool's NAME, DESCRIPTION (docstring) and INPUT SCHEMA, and
decides on its own which to call.

Run:  python steps/step3_prebuilt_agent.py
Needs OPENAI_API_KEY in your .env file.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()
SERVER = Path(__file__).resolve().parent.parent / "mcp_server" / "helpdesk_server.py"


async def main():
    client = MultiServerMCPClient({
        "helpdesk": {"transport": "stdio", "command": sys.executable, "args": [str(SERVER)]}
    })
    tools = await client.get_tools()

    agent = create_agent(
        model=os.getenv("MODEL", "openai:gpt-4.1-mini"),   # "provider:model-name"
        tools=tools,
        system_prompt="You are a friendly IT help desk agent. Search the knowledge base before raising tickets.",
    )

    question = "My VPN won't connect. Is something wrong with it?"
    print(f"USER: {question}\n")
    result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]})

    # result["messages"] is the full conversation: user, AI tool-calls, tool results, final answer.
    for msg in result["messages"]:
        if msg.type == "ai" and msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"  [agent calls tool] {tc['name']}({tc['args']})")
        elif msg.type == "tool":
            print(f"  [tool result] {str(msg.content)[:80]}...")
    print(f"\nAGENT: {result['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
