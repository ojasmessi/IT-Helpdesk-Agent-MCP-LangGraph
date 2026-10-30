"""
STEP 6 — A web chat UI with Chainlit
=====================================

Same graph as the CLI, now in a browser chat window with:
  * tool calls shown as collapsible "steps",
  * Approve / Reject buttons when a sensitive tool needs sign-off,
  * a separate memory thread per browser session.

Run:  chainlit run app_chainlit.py -w        (then open http://localhost:8000)
      (-w reloads the app when you edit the file)
"""

import uuid

import chainlit as cl
from dotenv import load_dotenv
from langgraph.types import Command

from agent.graph import build_graph

load_dotenv()


@cl.on_chat_start
async def start():
    """Runs once per browser session: build the graph and give it a fresh thread."""
    cl.user_session.set("graph", await build_graph())
    cl.user_session.set("config", {"configurable": {"thread_id": str(uuid.uuid4())}})
    await cl.Message(
        content="👋 Hi, I'm **Desk**, your IT help desk assistant. What's going wrong today?"
    ).send()


async def run_turn(payload):
    """Stream graph updates into the UI. Returns an interrupt payload if paused."""
    graph = cl.user_session.get("graph")
    config = cl.user_session.get("config")
    async for update in graph.astream(payload, config, stream_mode="updates"):
        for node, data in update.items():
            if node == "__interrupt__":
                return data[0].value
            for msg in (data or {}).get("messages", []):
                if msg.type == "ai" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        async with cl.Step(name=f"🔧 {tc['name']}", type="tool") as step:
                            step.input = tc["args"]
                elif msg.type == "tool":
                    async with cl.Step(name=f"✅ {msg.name} result", type="tool") as step:
                        step.output = msg.text if hasattr(msg, "text") else str(msg.content)
                elif msg.type == "ai" and msg.content:
                    await cl.Message(content=msg.content).send()
    return None


@cl.on_message
async def on_message(message: cl.Message):
    pending = await run_turn({"messages": [{"role": "user", "content": message.content}]})

    while pending:   # human-in-the-loop: show buttons, resume the graph with the choice
        res = await cl.AskActionMessage(
            content=f"⚠️ **Approval needed:** `{pending['tool']}` with `{pending['args']}`",
            actions=[
                cl.Action(name="approve", payload={"decision": "approve"}, label="✅ Approve"),
                cl.Action(name="reject", payload={"decision": "reject"}, label="❌ Reject"),
            ],
            timeout=300,
        ).send()
        decision = res["payload"]["decision"] if res else "reject"
        pending = await run_turn(Command(resume=decision))
