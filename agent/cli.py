"""
STEP 5 — Chat with your agent in the terminal
=============================================

Run from the project root (MCP server must be running first):
    Terminal 1:  python mcp_server/helpdesk_server.py
    Terminal 2:  python -m agent.cli
Type 'quit' to exit, 'new' to start a fresh conversation.

What to notice:
  * The agent REMEMBERS earlier messages — same thread_id = same conversation.
  * Ask "I'm locked out, my username is alice" — the graph PAUSES and asks YOU
    (the human approver) before unlock_account runs.
"""

import asyncio
import uuid

from dotenv import load_dotenv
from langgraph.types import Command

from agent.graph import build_graph

load_dotenv()


async def run_turn(graph, payload, config):
    """Stream one turn. Returns an interrupt payload if the graph paused."""
    async for update in graph.astream(payload, config, stream_mode="updates"):
        for node, data in update.items():
            if node == "__interrupt__":
                return data[0].value          # graph paused for approval
            for msg in (data or {}).get("messages", []):
                if msg.type == "ai" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        print(f"   🔧 {tc['name']}({tc['args']})")
                elif msg.type == "ai" and msg.content:
                    print(f"\nDesk: {msg.content}\n")
    return None


async def main():
    graph = await build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    print("IT Help Desk agent ready. Type 'quit' to exit, 'new' for a new conversation.\n")

    while True:
        user = input("You: ").strip()
        if user.lower() in {"quit", "exit"}:
            break
        if user.lower() == "new":
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            print("(new conversation)\n")
            continue
        if not user:
            continue

        pending = await run_turn(graph, {"messages": [{"role": "user", "content": user}]}, config)
        while pending:   # keep handling approvals until the graph finishes
            print(f"\n⚠️  APPROVAL NEEDED: {pending['question']}")
            decision = input("Approver (approve/reject): ").strip()
            pending = await run_turn(graph, Command(resume=decision), config)


if __name__ == "__main__":
    asyncio.run(main())
