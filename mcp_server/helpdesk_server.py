"""
STEP 1 — The MCP server (the "hands" of your agent)
====================================================

An MCP server is a small program that exposes TOOLS (actions), RESOURCES
(read-only data) and PROMPTS (templates) over the Model Context Protocol.
Any MCP client — your LangGraph agent, Claude Desktop, Cursor — can connect
to it and use those tools without knowing how they are implemented.

This server pretends to be a company's IT back-office:
  * a ticketing system   (SQLite database file: helpdesk.db)
  * a knowledge base     (a small Python list of troubleshooting articles)
  * a service-status page
  * an identity system   (password resets / account unlocks)

TRANSPORT: SSE (Server-Sent Events) over HTTP
This server runs as its own web service. Start it FIRST, in its own terminal,
and leave it running; agents and UIs connect to it by URL:

    python mcp_server/helpdesk_server.py          # SSE at http://127.0.0.1:8001/sse

Host, port and transport come from .env (see mcp_config.py). Set
MCP_TRANSPORT=streamable_http to serve the newer transport at /mcp instead.

Test it by hand with the MCP Inspector (server must be running):
    npx @modelcontextprotocol/inspector
    -> Transport: SSE, URL: http://127.0.0.1:8001/sse, Connect

We still log to stderr rather than print(): it keeps logs separate and is
good habit for any MCP server.
"""

import logging
import secrets
import sqlite3
import string
import sys
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Make the project root importable so we can share settings with the clients.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mcp_config  # noqa: E402

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="[helpdesk-mcp] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# 1. Create the server. The name is what clients see.
#    host/port tell the built-in web server (Uvicorn) where to listen.
#    127.0.0.1 = only this computer can connect. Use 0.0.0.0 only behind auth/firewall.
mcp = FastMCP("it-helpdesk", host=mcp_config.HOST, port=mcp_config.PORT)

DB_PATH = Path(__file__).parent / "helpdesk.db"

# ---------------------------------------------------------------------------
# Fake back-office data
# ---------------------------------------------------------------------------
KNOWLEDGE_BASE = [
    {"id": "KB001", "title": "VPN will not connect",
     "keywords": ["vpn", "connect", "remote", "globalprotect", "tunnel"],
     "solution": "1) Check your internet works without VPN. 2) Quit and reopen the VPN client. "
                 "3) Make sure your laptop date/time is correct (certificate checks fail otherwise). "
                 "4) Switch the gateway from 'Auto' to 'US-East'. 5) If it still fails, raise a ticket with the error code."},
    {"id": "KB002", "title": "Outlook keeps asking for my password",
     "keywords": ["outlook", "email", "password", "prompt", "mail"],
     "solution": "1) Close Outlook. 2) Open Credential Manager and remove entries starting with 'MicrosoftOffice'. "
                 "3) Reopen Outlook and sign in once with MFA. If your password recently expired, reset it first."},
    {"id": "KB003", "title": "Printer is offline",
     "keywords": ["printer", "print", "offline", "queue"],
     "solution": "1) Check the printer shows Ready on its screen. 2) Clear the print queue. "
                 "3) Remove and re-add the printer from \\\\printsrv01. 4) Restart the Print Spooler service."},
    {"id": "KB004", "title": "Laptop is very slow",
     "keywords": ["slow", "laptop", "performance", "freeze", "lag", "cpu"],
     "solution": "1) Restart (not just sleep) the laptop. 2) Check Task Manager for apps using high CPU/RAM. "
                 "3) Install pending Windows updates. 4) Make sure at least 10% disk space is free."},
    {"id": "KB005", "title": "Account locked after failed logins",
     "keywords": ["locked", "lockout", "account", "login", "sign in"],
     "solution": "Accounts lock after 5 failed attempts and auto-unlock after 30 minutes. "
                 "The help desk can unlock it immediately after verifying your identity."},
    {"id": "KB006", "title": "Suspicious / phishing email received",
     "keywords": ["phishing", "suspicious", "scam", "spam", "link", "attachment"],
     "solution": "Do NOT click links or open attachments. Use the 'Report Phishing' button in Outlook. "
                 "If you already clicked or entered your password, reset your password now and raise a P1 security ticket."},
]

SERVICES = {
    "email": "operational",
    "vpn": "degraded — EU-West gateway has intermittent drops, engineers investigating",
    "wifi": "operational",
    "sharepoint": "operational",
    "printing": "operational",
}

# Fake directory of users that exist in the identity system
USERS = {"ojas": "Ojas", "alice": "Alice Murphy", "bob": "Bob Kelly"}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            category TEXT NOT NULL,
            priority TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        )""")
    return conn


# ---------------------------------------------------------------------------
# TOOLS — functions the LLM is allowed to call.
# The function name becomes the tool name, the type hints become the input
# schema, and the DOCSTRING becomes the description the LLM reads when it
# decides which tool to use. Good docstrings = a smarter agent.
# ---------------------------------------------------------------------------
@mcp.tool()
def search_knowledge_base(query: str) -> str:
    """Search the IT knowledge base for troubleshooting articles.
    Use this FIRST for any technical problem before creating a ticket.

    Args:
        query: A short description of the problem, e.g. "vpn not connecting".
    """
    words = set(query.lower().split())
    scored = []
    for art in KNOWLEDGE_BASE:
        haystack = set(" ".join(art["keywords"] + [art["title"].lower()]).split())
        score = len(words & haystack)
        if score:
            scored.append((score, art))
    scored.sort(key=lambda x: x[0], reverse=True)
    log.info("KB search %r -> %d hits", query, len(scored))
    if not scored:
        return "No matching knowledge base articles found."
    return "\n\n".join(f"[{a['id']}] {a['title']}\n{a['solution']}" for _, a in scored[:2])


@mcp.tool()
def check_service_status(service: str = "all") -> str:
    """Check whether a company IT service is up, degraded or down.

    Args:
        service: one of email, vpn, wifi, sharepoint, printing — or "all".
    """
    service = service.lower().strip()
    if service == "all":
        return "\n".join(f"{name}: {state}" for name, state in SERVICES.items())
    if service not in SERVICES:
        return f"Unknown service '{service}'. Known services: {', '.join(SERVICES)}"
    return f"{service}: {SERVICES[service]}"


@mcp.tool()
def create_ticket(username: str, category: str, priority: str, summary: str) -> str:
    """Create a help desk ticket when the knowledge base did not fix the problem.

    Args:
        username: the employee's username, e.g. "ojas".
        category: one of hardware, software, network, access, security.
        priority: P1 (critical/security), P2 (high), P3 (normal), P4 (low).
        summary: one or two sentences describing the issue and what was already tried.
    """
    category, priority = category.lower(), priority.upper()
    if category not in {"hardware", "software", "network", "access", "security"}:
        return "Error: category must be hardware, software, network, access or security."
    if priority not in {"P1", "P2", "P3", "P4"}:
        return "Error: priority must be P1, P2, P3 or P4."
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (username, category, priority, summary, created_at) VALUES (?,?,?,?,?)",
            (username.lower(), category, priority, summary, datetime.now().isoformat(timespec="seconds")))
        ticket_id = cur.lastrowid
    log.info("Created ticket INC%05d", ticket_id)
    return f"Ticket INC{ticket_id:05d} created ({priority}, {category}). An engineer will be in touch."


@mcp.tool()
def get_ticket_status(ticket_id: str) -> str:
    """Look up a ticket's status by its number, e.g. "INC00003" or "3"."""
    digits = "".join(ch for ch in ticket_id if ch.isdigit())
    if not digits:
        return "Error: give a ticket number like INC00003."
    with _db() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (int(digits),)).fetchone()
    if not row:
        return f"No ticket found with number {ticket_id}."
    return (f"INC{row['id']:05d} | {row['status']} | {row['priority']} | {row['category']} | "
            f"opened {row['created_at']} by {row['username']}\nSummary: {row['summary']}")


@mcp.tool()
def list_user_tickets(username: str) -> str:
    """List all tickets raised by a given username."""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM tickets WHERE username = ? ORDER BY id DESC",
                            (username.lower(),)).fetchall()
    if not rows:
        return f"No tickets found for {username}."
    return "\n".join(f"INC{r['id']:05d} | {r['status']} | {r['priority']} | {r['summary'][:60]}" for r in rows)


@mcp.tool()
def unlock_account(username: str) -> str:
    """Unlock a user's locked account. SENSITIVE: requires human approval in the agent."""
    if username.lower() not in USERS:
        return f"Error: user '{username}' not found in the directory."
    log.warning("Account unlocked for %s", username)
    return f"Account for {USERS[username.lower()]} ({username}) has been unlocked."


@mcp.tool()
def reset_password(username: str) -> str:
    """Reset a user's password and issue a one-time temporary password.
    SENSITIVE: requires human approval in the agent."""
    if username.lower() not in USERS:
        return f"Error: user '{username}' not found in the directory."
    alphabet = string.ascii_letters + string.digits
    temp = "".join(secrets.choice(alphabet) for _ in range(12))
    log.warning("Password reset for %s", username)
    return (f"Password reset for {username}. Temporary password: {temp} "
            "(must be changed at next login, expires in 24h).")


# ---------------------------------------------------------------------------
# RESOURCE — read-only data a client can fetch by URI (not called by the LLM
# automatically like tools; the app decides when to load it).
# ---------------------------------------------------------------------------
@mcp.resource("helpdesk://policies/sla")
def sla_policy() -> str:
    """Help desk response-time targets."""
    return "P1: 1 hour | P2: 4 hours | P3: 1 business day | P4: 3 business days"


# ---------------------------------------------------------------------------
# PROMPT — a reusable prompt template the server offers to clients.
# ---------------------------------------------------------------------------
@mcp.prompt()
def triage_prompt(issue: str) -> str:
    """Template for triaging a new IT issue."""
    return f"Triage this IT issue. Suggest category and priority (P1-P4) and explain why: {issue}"


if __name__ == "__main__":
    # SSE endpoints:  GET /sse  (event stream)  +  POST /messages/  (client requests)
    # Streamable HTTP endpoint: /mcp
    transport = "sse" if mcp_config.TRANSPORT == "sse" else "streamable-http"
    path = mcp.settings.sse_path if transport == "sse" else mcp.settings.streamable_http_path
    log.info("Starting it-helpdesk MCP server (%s) at http://%s:%s%s",
             transport, mcp_config.HOST, mcp_config.PORT, path)
    try:
        mcp.run(transport=transport)
    except KeyboardInterrupt:
        log.info("Server stopped by user (Ctrl+C).")
