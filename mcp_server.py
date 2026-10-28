"""
ShopEase Support MCP Server
---------------------------
Exposes the company's live order, shipment, inventory, refund and ticketing
systems as MCP tools so that any MCP-capable agent framework (AutoGen here)
can use them without custom glue code.

What is MCP?
    Model Context Protocol is an open standard that lets an AI agent (the
    "client") discover and call functions (the "server's" tools) in a uniform
    way. The server advertises each tool's name, description and parameter
    types; the agent's LLM reads that and decides when to call which tool.
    This file is the server side. Three kinds of things can be exposed:
      - tools     : functions the agent can call (@mcp.tool)
      - resources : read-only data/documents the agent can read (@mcp.resource)
      - prompts   : reusable prompt templates (not used here)

Run standalone (stdio, the default used by AutoGen's StdioServerParams):
    python mcp_server/support_server.py

Run as a network service (many agents / many machines can share it):
    python mcp_server/support_server.py --transport sse --port 8765
"""

import argparse       # Read command-line options (--transport, --port)
import functools      # Keep tool signatures intact when wrapping them (audit log)
import json           # Convert Python dictionaries into JSON strings
import math           # Reject NaN / infinite refund amounts
import os             # Read the caller identity and DB override from the environment
import re             # Validate ids and strip control characters
import sqlite3        # Use SQLite database operations
from datetime import datetime, timedelta  # Work with dates and time periods
from pathlib import Path                  # Create safe file paths

from mcp.server.fastmcp import FastMCP    # High-level helper for building an MCP server

# SQLite file lives next to this script, regardless of the current working dir
# (SHOPEASE_DB lets tests and evals point the server at a throwaway copy).
DB_PATH = Path(os.getenv("SHOPEASE_DB") or Path(__file__).parent / "shopease.db")
REFUND_WINDOW_DAYS = 30
AUTO_REFUND_LIMIT = 200.00  # anything above this needs a human

# --------------------------------------------------------------------------
# Security
# SCOPE is who this server instance acts for. The client app (never the LLM) sets
# SHOPEASE_CUSTOMER when it launches the server for a signed-in customer, so no prompt or
# tool argument can widen it. Empty or "*" = unrestricted (staff, local CLI, shared SSE server).
# --------------------------------------------------------------------------
SCOPE = os.getenv("SHOPEASE_CUSTOMER", "").strip().lower()
if SCOPE == "*":
    SCOPE = ""

DENIED = "ACCESS DENIED: you can only access the signed-in customer's own account."
ORDER_ID_RE = re.compile(r"^ORD-\d{1,8}$")
TRACKING_RE = re.compile(r"^TRK-\d{1,8}$")
SKU_RE = re.compile(r"^[A-Z]{2}-\d{1,6}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
MAX_TEXT = 500
MAX_TICKETS_PER_DAY = 5  # per customer, so the agents can't be used to flood the help desk

# The server object. The name is what clients see when they connect.
mcp = FastMCP("shopease-support")


def _clean(text: str, limit: int = MAX_TEXT) -> str:
    """Strip control characters and cap the length of free text before it is stored."""
    return CONTROL_CHARS_RE.sub(" ", str(text)).strip()[:limit]


def audited(fn):
    """Record every tool call (who, what, outcome) in the audit table for later review."""

    @functools.wraps(fn)  # keeps the signature + docstring FastMCP reads to build the tool schema
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
                    "actor TEXT, tool TEXT, args TEXT, outcome TEXT)"
                )
                conn.execute(
                    "INSERT INTO audit (ts, actor, tool, args, outcome) VALUES (?,?,?,?,?)",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        SCOPE or "staff",
                        fn.__name__,
                        json.dumps(kwargs or list(args), default=str)[:1000],
                        str(result)[:200],
                    ),
                )
        except sqlite3.Error:
            pass  # auditing must never break a customer's request
        return result

    return wrapper


# --------------------------------------------------------------------------
# Database (stands in for the real order / WMS / ticketing back-ends)
# --------------------------------------------------------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts (row["total"], dict(row))
    return conn


def seed_db() -> None:
    """Create the tables and demo data on first run only."""
    if DB_PATH.exists():  # already seeded, don't overwrite existing data
        return
    now = datetime.now()
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY, email TEXT, sku TEXT, qty INTEGER,
            total REAL, status TEXT, tracking_id TEXT, placed_at TEXT,
            delivered_at TEXT
        );
        CREATE TABLE inventory (
            sku TEXT PRIMARY KEY, name TEXT, stock INTEGER, warehouse TEXT,
            restock_date TEXT
        );
        CREATE TABLE refunds (
            refund_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT,
            amount REAL, reason TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
            summary TEXT, priority TEXT, status TEXT, created_at TEXT
        );
        """
    )
    # Helper: ISO timestamp for "N days ago", so demo data is always relative to today
    d = lambda days: (now - timedelta(days=days)).isoformat(timespec="minutes")
    # Columns: order_id, email, sku, qty, total, status, tracking_id, placed_at, delivered_at
    conn.executemany(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("ORD-1001", "priya@example.com", "HP-200", 1, 89.99, "delivered", "TRK-9001", d(12), d(8)),
            ("ORD-1002", "priya@example.com", "SW-450", 1, 249.00, "in_transit", "TRK-9002", d(3), None),
            ("ORD-1003", "arjun@example.com", "KB-310", 2, 119.98, "processing", None, d(0), None),
            # Delivered 40 days ago -> outside the 30-day refund window (tests rejection)
            ("ORD-1004", "arjun@example.com", "HP-200", 1, 89.99, "delivered", "TRK-9004", d(45), d(40)),
            # Total > AUTO_REFUND_LIMIT -> refund goes to human review
            ("ORD-1005", "meera@example.com", "LT-900", 1, 1299.00, "delivered", "TRK-9005", d(6), d(2)),
        ],
    )
    conn.executemany(
        "INSERT INTO inventory VALUES (?,?,?,?,?)",
        [
            ("HP-200", "Wireless Headphones", 42, "Chennai-WH1", None),
            # Out of stock with a restock date 5 days away
            ("SW-450", "Smart Watch Pro", 0, "Bengaluru-WH2", (now + timedelta(days=5)).date().isoformat()),
            ("KB-310", "Mechanical Keyboard", 7, "Chennai-WH1", None),
            ("LT-900", "UltraBook 14", 3, "Mumbai-WH3", None),
        ],
    )
    conn.commit()
    conn.close()


def _simulated_carrier_events(placed_at: str, delivered_at: str | None) -> list[dict]:
    """Fake a carrier API: events unlock as real time passes since the order."""
    placed = datetime.fromisoformat(placed_at)
    # (time after order placed, status, location)
    timeline = [
        (timedelta(hours=2), "Picked up", "Origin warehouse"),
        (timedelta(hours=20), "In transit", "Regional hub"),
        (timedelta(days=2), "Arrived at facility", "Destination city hub"),
        (timedelta(days=3), "Out for delivery", "Local courier"),
    ]
    now = datetime.now()
    # Keep only the events whose time has already passed
    events = [
        {"time": (placed + off).isoformat(timespec="minutes"), "status": s, "location": loc}
        for off, s, loc in timeline
        if placed + off <= now
    ]
    if delivered_at:  # final event comes from the real delivery timestamp
        events.append({"time": delivered_at, "status": "Delivered", "location": "Customer address"})
    return events


# --------------------------------------------------------------------------
# MCP tools
# The @mcp.tool() decorator registers each function as a tool. FastMCP builds
# the tool schema from the function name, type hints and docstring - the
# docstring is what the LLM reads to decide when to use the tool, so keep it
# clear. Tools return strings (JSON text or a plain message) for the LLM.
# --------------------------------------------------------------------------
@mcp.tool()
@audited
def list_customer_orders(email: str) -> str:
    """List all orders for a customer email (most recent first)."""
    email = email.strip().lower()
    if SCOPE and email != SCOPE:
        return DENIED
    # `with get_conn()` commits/rolls back the transaction on exit
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT order_id, sku, total, status, placed_at FROM orders "
            "WHERE email = ? ORDER BY placed_at DESC",
            (email.lower(),),  # "?" placeholders prevent SQL injection
        ).fetchall()
    if not rows:
        return f"No orders found for {email}."
    return json.dumps([dict(r) for r in rows], indent=2)


@mcp.tool()
@audited
def get_order(order_id: str) -> str:
    """Get full details of one order: items, amount, status, tracking id, dates."""
    order_id = order_id.strip().upper()
    if not ORDER_ID_RE.match(order_id):
        return "Invalid order id. Expected a format like ORD-1001."
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    # Someone else's order looks exactly like a missing one, so ids can't be probed.
    if not row or (SCOPE and row["email"] != SCOPE):
        return f"Order {order_id} not found."
    return json.dumps(dict(row), indent=2)


@mcp.tool()
@audited
def track_shipment(tracking_id: str) -> str:
    """Get live carrier tracking events for a shipment."""
    tracking_id = tracking_id.strip().upper()
    if not TRACKING_RE.match(tracking_id):
        return "Invalid tracking id. Expected a format like TRK-9001."
    with get_conn() as conn:
        row = conn.execute(
            "SELECT order_id, email, placed_at, delivered_at FROM orders WHERE tracking_id = ?",
            (tracking_id,),
        ).fetchone()
    if not row or (SCOPE and row["email"] != SCOPE):
        return f"Tracking id {tracking_id} not found."
    events = _simulated_carrier_events(row["placed_at"], row["delivered_at"])
    # Only undelivered orders get an ETA: placed date + 4 days
    eta = None
    if not row["delivered_at"]:
        eta = (datetime.fromisoformat(row["placed_at"]) + timedelta(days=4)).date().isoformat()
    return json.dumps(
        {"tracking_id": tracking_id, "order_id": row["order_id"], "events": events, "eta": eta},
        indent=2,
    )


@mcp.tool()
@audited
def check_inventory(sku: str) -> str:
    """Check real-time stock level, warehouse and restock date for a product SKU."""
    sku = sku.strip().upper()
    if not SKU_RE.match(sku):
        return "Invalid SKU. Expected a format like HP-200."
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM inventory WHERE sku = ?", (sku,)).fetchone()
    if not row:
        return f"SKU {sku} not found."
    if SCOPE:  # customers only learn availability, not warehouse stock levels or locations
        return json.dumps(
            {"sku": row["sku"], "name": row["name"], "in_stock": row["stock"] > 0, "restock_date": row["restock_date"]},
            indent=2,
        )
    return json.dumps(dict(row), indent=2)


@mcp.tool()
@audited
def issue_refund(order_id: str, amount: float, reason: str) -> str:
    """Issue a refund for an order. Enforces the refund policy server-side:
    order must be delivered, within the refund window, amount <= order total,
    and amounts above the auto-approval limit are queued for a human."""
    # Business rules live HERE, not in the agent's prompt: even if the LLM is
    # tricked or makes a mistake, the server still refuses invalid refunds.
    order_id = order_id.strip().upper()
    if not ORDER_ID_RE.match(order_id):
        return "REJECTED: invalid order id. Expected a format like ORD-1001."
    if not math.isfinite(amount):
        return "REJECTED: amount must be a valid number."
    amount = round(amount, 2)
    reason = _clean(reason, 200) or "unspecified"
    with get_conn() as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if not order or (SCOPE and order["email"] != SCOPE):
            return f"REJECTED: order {order_id} not found."
        # Rule 1: only delivered orders can be refunded
        if order["status"] != "delivered":
            return f"REJECTED: order is '{order['status']}', refunds only apply to delivered orders."
        # Rule 2: must be within the refund window (days since delivery)
        age = (datetime.now() - datetime.fromisoformat(order["delivered_at"])).days
        if age > REFUND_WINDOW_DAYS:
            return f"REJECTED: delivered {age} days ago, outside the {REFUND_WINDOW_DAYS}-day window."
        # Rule 3: amount must be positive and not exceed what was paid
        if amount <= 0 or amount > order["total"]:
            return f"REJECTED: amount must be between 0 and order total {order['total']}."
        # Rule 4: only one non-rejected refund per order
        existing = conn.execute(
            "SELECT 1 FROM refunds WHERE order_id = ? AND status != 'rejected'", (order["order_id"],)
        ).fetchone()
        if existing:
            return "REJECTED: a refund already exists for this order."

        # Small refunds are approved instantly; big ones wait for a human
        status = "approved" if amount <= AUTO_REFUND_LIMIT else "pending_human_review"
        cur = conn.execute(
            "INSERT INTO refunds (order_id, amount, reason, status, created_at) VALUES (?,?,?,?,?)",
            (order["order_id"], amount, reason, status, datetime.now().isoformat(timespec="seconds")),
        )
    return json.dumps({"refund_id": cur.lastrowid, "order_id": order["order_id"], "amount": amount, "status": status})


@mcp.tool()
@audited
def cancel_order(order_id: str, reason: str = "") -> str:
    """Cancel an order that has not shipped yet (status 'processing'). Shipped or delivered orders cannot
    be cancelled: use a refund for delivered ones. Only call this when the customer clearly asks to cancel."""
    order_id = order_id.strip().upper()
    if not ORDER_ID_RE.match(order_id):
        return "REJECTED: invalid order id. Expected a format like ORD-1001."
    with get_conn() as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if not order or (SCOPE and order["email"] != SCOPE):
            return f"REJECTED: order {order_id} not found."
        # One atomic UPDATE so two simultaneous requests can't both succeed.
        cur = conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'processing'", (order_id,))
        if cur.rowcount != 1:
            return f"REJECTED: order is '{order['status']}'; only orders still processing can be cancelled."
    return json.dumps({"order_id": order_id, "status": "cancelled"})


@mcp.tool()
@audited
def create_ticket(email: str, summary: str, priority: str = "medium") -> str:
    """Create a support ticket for a human agent. priority: low | medium | high."""
    # A signed-in customer can only open tickets for themselves, whatever email the agent passes.
    email = SCOPE or email.strip().lower()
    if not EMAIL_RE.match(email):
        return "Invalid email address."
    summary = _clean(summary)
    if not summary:
        return "A ticket needs a short summary."
    # Fall back to "medium" if the agent passes an invalid priority
    if priority not in {"low", "medium", "high"}:
        priority = "medium"
    since = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    with get_conn() as conn:
        recent = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE email = ? AND created_at >= ?", (email, since)
        ).fetchone()[0]
        if recent >= MAX_TICKETS_PER_DAY:
            return "LIMIT REACHED: too many tickets opened in the last 24 hours. A human agent will follow up on the existing ones."
        cur = conn.execute(
            "INSERT INTO tickets (email, summary, priority, status, created_at) VALUES (?,?,?,?,?)",
            (email, summary, priority, "open", datetime.now().isoformat(timespec="seconds")),
        )
    return json.dumps({"ticket_id": cur.lastrowid, "priority": priority, "status": "open"})


# --------------------------------------------------------------------------
# MCP resource: policy text agents can read
# Resources are read-only content addressed by a URI (here policy://refunds).
# Unlike tools, the agent doesn't "do" anything - it just reads the text.
# --------------------------------------------------------------------------
@mcp.resource("policy://refunds")
def refund_policy() -> str:
    return (
        f"Refunds are allowed for delivered orders within {REFUND_WINDOW_DAYS} days of delivery. "
        f"Refunds up to {AUTO_REFUND_LIMIT:.2f} are auto-approved; larger ones need human review. "
        "One refund per order. Orders still processing can be cancelled instead."
    )


if __name__ == "__main__":
    # CLI options: choose how clients connect to this server
    #   stdio           -> client launches this script as a subprocess (local, default)
    #   sse/streamable-http -> runs as an HTTP service on --port (shared, remote)
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    seed_db()  # make sure the demo database exists before serving requests
    if args.transport != "stdio":  # port only matters for network transports
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)  # start serving; blocks until stopped
