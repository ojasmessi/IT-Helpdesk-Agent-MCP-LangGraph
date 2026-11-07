"""
STEP 4 — Build the agent graph yourself with LangGraph
=======================================================

create_agent() in Step 3 hid the graph from you. Here we build it by hand so
you understand (and can control) every part:

                ┌──────────────┐
    START ────► │   agent      │  (LLM decides: answer, or call tool(s)?)
                └──────┬───────┘
          tool call?   │   no tool call
          ┌────────────┴──────────────┐
          ▼                           ▼
   ┌──────────────┐                  END
   │   approval   │  (pauses with interrupt() if the tool is SENSITIVE)
   └──────┬───────┘
          ▼
   ┌──────────────┐
   │    tools     │  (runs the MCP tools, adds results to state)
   └──────┬───────┘
          └──────────► back to agent

Key LangGraph ideas used here:
  * STATE          – a typed dict that flows through the graph (our messages).
  * NODES          – plain functions that read state and return updates.
  * EDGES          – which node runs next; "conditional" edges decide at runtime.
  * CHECKPOINTER   – saves state after every step => conversation memory
                     (per thread_id) and the ability to pause/resume.
  * interrupt()    – pauses the graph and waits for a human (human-in-the-loop).
"""

import os
from typing import Annotated, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from mcp_config import load_tools

# Tools that change something important must be approved by a human first.
SENSITIVE_TOOLS = {"reset_password", "unlock_account"}

SYSTEM_PROMPT = """You are "Desk", the IT help desk assistant for a company.

How you work:
1. Understand the problem. Ask ONE short clarifying question if it is vague.
2. For outages/connectivity issues, check_service_status first.
3. Always search_knowledge_base and walk the user through the fix before raising a ticket.
4. If the fix does not work, create_ticket with a clear summary of what was tried.
   Priority: security incidents = P1, cannot work at all = P2, otherwise P3/P4.
5. Password resets and account unlocks need the user's username. A human
   approver will confirm these actions — tell the user that.
6. Never invent ticket numbers, statuses or temporary passwords — only report what tools return.
7. Be concise and friendly. Use numbered steps for instructions.
"""


# ---------------------------------------------------------------------------
# 1. STATE: what the graph remembers. add_messages APPENDS new messages
#    instead of overwriting the list (that's a "reducer").
# ---------------------------------------------------------------------------
class HelpdeskState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


async def build_graph(model=None, checkpointer=None):
    """Load MCP tools, then wire up and compile the LangGraph."""
    # Connect to the MCP server(s) over SSE — the server must already be running.
    tools = await load_tools()

    if model is None:
        model = init_chat_model(os.getenv("MODEL", "openai:gpt-4.1-mini"), temperature=0)
    # bind_tools tells the LLM which tools exist (names, descriptions, schemas).
    llm_with_tools = model.bind_tools(tools)

    # -----------------------------------------------------------------------
    # 2. NODES
    # -----------------------------------------------------------------------
    async def agent_node(state: HelpdeskState):
        """Ask the LLM what to do next."""
        messages = [SystemMessage(SYSTEM_PROMPT)] + state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def approval_node(state: HelpdeskState):
        """Human-in-the-loop gate for sensitive tools.

        interrupt() PAUSES the whole graph here and hands the payload to whoever
        is running it (the CLI or Chainlit UI). When they resume with
        Command(resume="approve"|"reject"), interrupt() returns that value and
        this function carries on from this exact line.
        """
        last: AIMessage = state["messages"][-1]
        rejected = []
        for call in last.tool_calls:
            if call["name"] in SENSITIVE_TOOLS:
                decision = interrupt({
                    "tool": call["name"],
                    "args": call["args"],
                    "question": f"Approve {call['name']} for {call['args']}? (approve/reject)",
                })
                if str(decision).strip().lower() not in {"approve", "approved", "yes", "y"}:
                    rejected.append(call)

        if not rejected:
            return {}   # nothing to change — continue to the tools node

        # Every tool call must get a ToolMessage answer. For rejected ones we
        # answer ourselves; the remaining approved/non-sensitive calls still run.
        rejected_ids = {c["id"] for c in rejected}
        kept = [c for c in last.tool_calls if c["id"] not in rejected_ids]
        updated_ai = AIMessage(content=last.content, tool_calls=kept, id=last.id)  # same id => replaces it
        denials = [ToolMessage(content="REJECTED by human approver. Do not retry; tell the user "
                                       "to contact the service desk by phone.",
                               tool_call_id=c["id"], name=c["name"]) for c in rejected]
        return {"messages": [updated_ai] + denials}

    # ToolNode is a prebuilt node that executes every tool call in the last AI message.
    # handle_tool_errors=True turns exceptions into messages the LLM can read and recover from.
    tool_node = ToolNode(tools, handle_tool_errors=True)

    # -----------------------------------------------------------------------
    # 3. EDGES (routing functions)
    # -----------------------------------------------------------------------
    def route_after_agent(state: HelpdeskState):
        last = state["messages"][-1]
        return "approval" if getattr(last, "tool_calls", None) else END

    def route_after_approval(state: HelpdeskState):
        last = state["messages"][-1]
        # If all calls were rejected, the last message is a ToolMessage -> back to the LLM.
        return "tools" if getattr(last, "tool_calls", None) else "agent"

    # -----------------------------------------------------------------------
    # 4. BUILD + COMPILE
    # -----------------------------------------------------------------------
    graph = StateGraph(HelpdeskState)
    graph.add_node("agent", agent_node)
    graph.add_node("approval", approval_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["approval", END])
    graph.add_conditional_edges("approval", route_after_approval, ["tools", "agent"])
    graph.add_edge("tools", "agent")

    # The checkpointer is what gives the agent memory between messages and lets
    # interrupt() pause/resume. InMemorySaver forgets on restart; swap for
    # SqliteSaver/PostgresSaver to persist.
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
