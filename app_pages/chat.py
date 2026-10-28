"""Support chat: guided request form + free-text chat with the AI agent team."""

import asyncio
import re
import sqlite3

import pandas as pd
import streamlit as st
from autogen_agentchat.messages import (
    TextMessage,
    ToolCallExecutionEvent,
    ToolCallRequestEvent,
)

import auth
import desk
from app import DEMO_SCENARIOS, build_team, scoped_task

MAX_MESSAGE_CHARS = 1000

PROBLEMS = {
    "Track my order": ":material/local_shipping:",
    "Request a refund": ":material/payments:",
    "Item arrived damaged": ":material/broken_image:",
    "Cancel an order": ":material/cancel:",
    "Is it in stock?": ":material/inventory_2:",
    "Something else": ":material/chat:",
}
ORDER_PROBLEMS = {"Track my order", "Request a refund", "Item arrived damaged", "Cancel an order"}
REFUND_REASONS = ["Stopped working", "Wrong item received", "Not as described", "Changed my mind"]

user = st.session_state.user
is_staff = user["role"] == "staff"

if not desk.db_ready():
    st.info("The shop database hasn't been created yet. Run `python mcp_server.py` once, then refresh.", title="No data yet")
    st.stop()

# Saved conversation: restored after a refresh or a new sign-in.
if not st.session_state.get("history_loaded"):
    st.session_state.events = desk.load_history(user)
    st.session_state.history_loaded = True


# ---------- data access (scoped by the signed-in user) ----------

@st.cache_data(ttl=5, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Read-only, parameterised lookup (short ttl so status changes show up quickly)."""
    with sqlite3.connect(f"file:{desk.DB_PATH}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(sql, conn, params=params)


owner = None if is_staff else user["email"]  # None = unrestricted

orders = query(
    "SELECT o.order_id, o.email, o.qty, o.total, o.status, i.name, i.sku "
    "FROM orders o JOIN inventory i ON i.sku = o.sku "
    "WHERE (? IS NULL OR o.email = ?) ORDER BY o.order_id",
    (owner, owner),
)
inventory = query("SELECT sku, name FROM inventory ORDER BY name")  # public catalogue: no stock counts

use_sse = False
scenario_prompt = None

with st.sidebar:
    if st.button("Clear chat", icon=":material/delete:", width="stretch", help="Deletes your saved conversation"):
        desk.clear_history(user)
        st.session_state.events = []
        st.rerun()
    if is_staff:  # infrastructure + demo data are staff-only
        st.divider()
        use_sse = st.toggle("Use SSE server", help="Connect to http://localhost:8765/sse instead of spawning the MCP server.")
        st.caption("Demo scenarios")
        for i, scenario in enumerate(DEMO_SCENARIOS, start=1):
            if st.button(f"Scenario {i}", key=f"demo_{i}", help=scenario, width="stretch"):
                scenario_prompt = scenario

st.caption(f"Hi {user['name'] or user['email']}. Pick your problem below, or just type in the chat.")


# ---------- guided request ----------

def order_label(order_id: str) -> str:
    row = orders.set_index("order_id").loc[order_id]
    return f"{order_id} · {row['name']} · {row['status'].replace('_', ' ')}"


def guided_request() -> str | None:
    """Dropdown-driven form that turns choices into a customer message. Returns it on send."""
    # Order-related options only make sense for accounts that have orders.
    available = list(PROBLEMS) if not orders.empty else ["Is it in stock?", "Something else"]

    with st.container(border=True):
        # Customers are fixed to their own account; staff can act for any customer.
        if is_staff:
            email = st.selectbox("Customer", sorted(orders["email"].unique()) or [user["email"]], key="g_email")
        else:
            email = user["email"]
        if orders.empty:
            st.caption("You have no orders yet.")
        problem = st.segmented_control(
            "What do you need help with?",
            available,
            format_func=lambda p: f"{PROBLEMS[p]} {p}",
            default=available[0],
            key="g_problem",
        )

        message = None
        mine = orders[orders["email"] == email]

        if problem in ORDER_PROBLEMS:
            choices = mine[mine["status"] == "processing"] if problem == "Cancel an order" else mine
            if choices.empty:
                st.info(
                    "Only orders that are still processing can be cancelled. For a delivered item, choose a refund instead.",
                    title="Nothing to cancel",
                )
                return None
            order_id = st.selectbox("Which order?", choices["order_id"], format_func=order_label, key=f"g_order_{email}_{problem}")
            order = mine.set_index("order_id").loc[order_id]
            cols = st.columns(3)
            cols[0].metric("Item", f"{order['name']} × {order['qty']}")
            cols[1].metric("Total", f"${order['total']:,.2f}")
            cols[2].metric("Status", order["status"].replace("_", " ").title())

            if problem == "Track my order":
                message = f"Hi, I'm {email}. Where is my order {order_id} ({order['name']})?"
            elif problem == "Cancel an order":
                message = f"{email} here. Please cancel my order {order_id} ({order['name']})."
            else:
                damaged = problem == "Item arrived damaged"
                reason = "Arrived damaged" if damaged else st.selectbox("Reason", REFUND_REASONS, key="g_reason")
                amount = st.number_input(
                    "Refund amount ($)", min_value=0.01, max_value=float(order["total"]), value=float(order["total"]), step=1.0, key=f"g_amt_{order_id}"
                )
                details = st.text_input("Anything else we should know?", key="g_details", max_chars=200)
                message = (
                    f"{email} here. {reason} for order {order_id} ({order['name']}). "
                    f"Please refund ${amount:,.2f}. {details}"
                ).strip()

        elif problem == "Is it in stock?":
            sku = st.selectbox(
                "Which product?", inventory["sku"], format_func=lambda s: inventory.set_index("sku").loc[s, "name"], key="g_sku"
            )
            name = inventory.set_index("sku").loc[sku, "name"]
            message = f"Hi, I'm {email}. Is the {name} ({sku}) in stock? If not, when will it be back?"

        elif problem == "Something else":
            text = st.text_area("Describe the problem", key="g_text", max_chars=MAX_MESSAGE_CHARS)
            message = f"I'm {email}. {text}" if text.strip() else None

        if st.button("Send to support team", icon=":material/send:", type="primary", disabled=not message):
            return message
    return None


# ---------- chat ----------

def render(event: dict) -> None:
    if event["kind"] == "user":
        with st.chat_message("user"):
            st.write(event["text"])
    elif event["kind"] == "agent":
        with st.chat_message("assistant"):
            st.caption(event["source"])
            st.write(event["text"])
    elif is_staff:  # raw tool calls/results can contain other data; customers don't see them
        with st.status(event["source"], type="step", state="complete"):
            st.code(event["text"], language="json")


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ORDER_RE = re.compile(r"\bORD-\d+\b", re.IGNORECASE)


def scrub(text: str) -> str:
    """Last line of defence for customers: mask any other account's email or order id in a reply."""
    if is_staff:
        return text
    own_orders = {o.upper() for o in orders["order_id"]}
    text = EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).lower() == user["email"] else "[hidden]", text)
    return ORDER_RE.sub(lambda m: m.group(0) if m.group(0).upper() in own_orders else "[hidden]", text)


async def run_turn(task: str) -> None:
    """Run the agent team on one customer message, rendering events as they stream in."""
    team = await build_team(use_sse, None if is_staff else user["email"])
    async for msg in team.run_stream(task=task):
        event = None
        if isinstance(msg, TextMessage) and msg.source != "user":
            text = scrub(msg.content)
            event = {"id": desk.save_event(user, "agent", msg.source, text), "kind": "agent", "source": msg.source, "text": text}
        elif is_staff and isinstance(msg, ToolCallRequestEvent):
            calls = "\n".join(f"{c.name}({c.arguments})" for c in msg.content)
            event = {"id": None, "kind": "tool", "source": f"{msg.source} called a tool", "text": calls}
        elif is_staff and isinstance(msg, ToolCallExecutionEvent):
            results = "\n".join(str(r.content) for r in msg.content)
            event = {"id": None, "kind": "tool", "source": f"{msg.source} got a result", "text": results}
        if event:
            st.session_state.events.append(event)
            render(event)


def rateable_reply_id() -> int | None:
    """Id of the latest agent reply if it closed the conversation with RESOLVED (so we can ask for a rating)."""
    for e in reversed(st.session_state.events):
        if e["kind"] == "agent" and e.get("id"):
            return e["id"] if "RESOLVED" in e["text"] else None
    return None


guided_prompt = guided_request()

for event in st.session_state.events:
    render(event)

reply_id = None if is_staff else rateable_reply_id()  # staff chat on behalf of customers, so only customers rate
if reply_id and not desk.has_rated(user, "chat", str(reply_id)):
    with st.container(border=True):
        st.caption("How was this answer?")
        stars = st.feedback("stars", key=f"rating_{reply_id}")
        if stars is not None:
            desk.submit_csat(user, "chat", str(reply_id), stars + 1)
            st.toast("Thanks for your feedback!")
            st.rerun()

prompt = (
    st.chat_input("Or type your question here", submit_mode="disable", max_chars=MAX_MESSAGE_CHARS)
    or guided_prompt
    or scenario_prompt
)

if prompt and not is_staff and not auth.allow_request(user["email"]):
    st.warning("You've sent a lot of messages recently. Please try again later.", title="Slow down a little")
    prompt = None

if prompt:
    prompt = prompt[:MAX_MESSAGE_CHARS]
    history = None if is_staff else desk.recent_context(user)  # taken before this message is saved
    user_event = {"id": desk.save_event(user, "user", "user", prompt), "kind": "user", "source": "user", "text": prompt}
    st.session_state.events.append(user_event)
    render(user_event)
    try:
        asyncio.run(run_turn(scoped_task(prompt, None if is_staff else user["email"], history)))
    except Exception as exc:
        # Internal error details (paths, tool names, server output) are for staff only.
        st.error(f"{type(exc).__name__}: {exc}" if is_staff else "Something went wrong. Please try again.", title="The support team couldn't finish")
    else:
        query.clear()  # order status may have changed (e.g. a cancellation)
        st.rerun()  # refresh the order data shown above
