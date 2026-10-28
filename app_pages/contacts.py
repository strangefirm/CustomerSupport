"""Staff contacts: a searchable customer list and a record page with everything we know about one customer."""

import pandas as pd
import streamlit as st

import auth
import desk

user = st.session_state.user
if not desk.is_staff(user):
    st.error("This page is only available to support staff.")
    st.stop()
if not desk.db_ready():
    st.info("The shop database hasn't been created yet. Run `python mcp_server.py` once, then refresh.", title="No data yet")
    st.stop()

TICKET_LABEL = {"open": "Open", "in_progress": "In progress", "resolved": "Resolved"}
STATUS_COLOR = {"open": "orange", "in_progress": "blue", "resolved": "green"}
names = auth.customer_names()


def display_name(email: str) -> str:
    return names.get(email) or email.split("@")[0].title()


search = st.text_input("Search contacts", placeholder="Search by email", key="contacts_search", max_chars=80, icon=":material/search:")
contacts = desk.list_contacts(user, search or None)
if not contacts:
    st.info("No contacts match that search.", title="Nothing here")
    st.stop()

table = pd.DataFrame(
    {
        "Name": [display_name(c["email"]) for c in contacts],
        "Email": [c["email"] for c in contacts],
        "Orders": [c["orders"] for c in contacts],
        "Lifetime value ($)": [round(c["lifetime_value"], 2) for c in contacts],
        "Open tickets": [c["open_tickets"] for c in contacts],
        "Last order": [(c["last_order"] or "")[:10] for c in contacts],
    }
)
st.caption(f"{len(contacts)} contact(s). Select a row to open the record.")
picked = st.dataframe(
    table, hide_index=True, on_select="rerun", selection_mode="single-row", key="contacts_table",
    alt="Customer contacts. Select a row to open the record.", height=280,
)
if picked.selection.rows:
    st.session_state.contact_email = table.iloc[picked.selection.rows[0]]["Email"]

email = st.session_state.get("contact_email")
contact = desk.get_contact(user, email) if email else None
if not contact:
    st.stop()

left, right = st.columns([1, 2.4], gap="medium")

with left, st.container(border=True):
    initials = "".join(p[0] for p in display_name(contact["email"]).split()[:2]).upper()
    st.markdown(f"### :orange-badge[{initials}] {display_name(contact['email'])}")
    st.text(contact["email"])
    st.divider()
    st.caption("About this contact")
    st.metric("Lifetime value", f"${contact['lifetime_value']:,.2f}")
    st.metric("Orders", len(contact["orders"]))
    st.metric("Open tickets", sum(1 for t in contact["tickets"] if t["status"] != "resolved"))
    st.metric("Satisfaction", f"{contact['csat_avg']:.1f} / 5" if contact["csat_avg"] is not None else "n/a")

with right:
    orders_tab, tickets_tab, refunds_tab, activity_tab = st.tabs(
        [f"Orders ({len(contact['orders'])})", f"Tickets ({len(contact['tickets'])})", f"Refunds ({len(contact['refunds'])})", "Activity"]
    )
    with orders_tab:
        if contact["orders"]:
            st.dataframe(
                pd.DataFrame(contact["orders"]).rename(columns=lambda c: c.replace("_", " ").title()),
                hide_index=True, alt="Orders for this contact",
            )
        else:
            st.caption("No orders.")
    with tickets_tab:
        if not contact["tickets"]:
            st.caption("No tickets.")
        for t in contact["tickets"]:
            with st.container(border=True):
                head, badge = st.columns([4, 1])
                head.markdown(f"**#{t['ticket_id']}** · priority {t['priority']}")
                badge.badge(TICKET_LABEL.get(t["status"], t["status"]), color=STATUS_COLOR.get(t["status"], "gray"))
                st.text(t["summary"][:200])  # plain text: customer-written
                if st.button("Open in inbox", key=f"open_{t['ticket_id']}", icon=":material/open_in_new:"):
                    st.session_state.inbox_ticket = t["ticket_id"]
                    st.switch_page("app_pages/inbox.py")
    with refunds_tab:
        if contact["refunds"]:
            st.dataframe(
                pd.DataFrame(contact["refunds"]).drop(columns=["reason"]).rename(columns=lambda c: c.replace("_", " ").title()),
                hide_index=True, alt="Refunds for this contact",
            )
        else:
            st.caption("No refunds.")
    with activity_tab:
        events = [(o["placed_at"], f"Placed order {o['order_id']} · {o['item']} · ${o['total']:,.2f}") for o in contact["orders"]]
        events += [(t["created_at"], f"Opened ticket #{t['ticket_id']}") for t in contact["tickets"]]
        events += [(r["created_at"], f"Refund #{r['refund_id']} {r['status'].replace('_', ' ')} · ${r['amount']:,.2f}") for r in contact["refunds"]]
        for when, what in sorted(events, reverse=True)[:40]:
            st.text(f"{when.replace('T', ' ')[:16]}   {what}")
