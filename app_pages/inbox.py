"""Staff ticket inbox: filter, open a ticket, reply, change status/priority, assign."""

import pandas as pd
import streamlit as st

import auth
import desk

user = st.session_state.user
if not desk.is_staff(user):  # defence in depth: the page is only listed for staff, but never trust that alone
    st.error("This page is only available to support staff.")
    st.stop()
if not desk.db_ready():
    st.info("The shop database hasn't been created yet. Run `python mcp_server.py` once, then refresh.", title="No data yet")
    st.stop()

LABELS = {"open": "Open", "in_progress": "In progress", "resolved": "Resolved"}
STATUS_COLOR = {"open": "orange", "in_progress": "blue", "resolved": "green"}
staff = auth.staff_emails()

opened = st.session_state.get("inbox_ticket")
if opened and st.button("Back to inbox", icon=":material/arrow_back:", key="inbox_back"):
    del st.session_state["inbox_ticket"]
    st.rerun()
browsing = not opened  # while a ticket is open, show it in place of the board / list

# ---------- filters ----------
with st.container(horizontal=True, vertical_alignment="bottom"):
    status = st.segmented_control("Status", ["Active", *LABELS.values()], default="Active", key="inbox_status")
    priority = st.selectbox("Priority", ["Any", *desk.PRIORITIES], key="inbox_priority")
    assignee = st.selectbox(
        "Assigned to", ["Anyone", "unassigned", *staff], format_func=lambda a: "Unassigned" if a == "unassigned" else a, key="inbox_assignee"
    )
    search = st.text_input("Search", placeholder="ticket #, email or text", key="inbox_search", max_chars=80)

wanted = {v: k for k, v in LABELS.items()}.get(status)
tickets = desk.list_tickets(
    user,
    status=wanted,
    priority=None if priority == "Any" else priority,
    assignee=None if assignee == "Anyone" else assignee,
    search=search or None,
)
if status == "Active":
    tickets = [t for t in tickets if t["status"] != "resolved"]

if not tickets and browsing:
    st.info("No tickets match these filters.", title="Nothing here")
    st.stop()

view = st.segmented_control("View", ["Board", "List"], default="Board", key="inbox_view", label_visibility="collapsed")
PRIORITY_COLOR = {"high": "red", "medium": "orange", "low": "gray"}

if browsing and view == "Board":  # pipeline columns, one per status
    cols = st.columns(len(LABELS), gap="small")
    for col, (key, label) in zip(cols, LABELS.items()):
        in_col = [t for t in tickets if t["status"] == key]
        with col:
            st.markdown(f"**{label}** · {len(in_col)}")
            for t in in_col[:12]:
                with st.container(border=True):
                    st.badge(t["priority"].title(), color=PRIORITY_COLOR[t["priority"]])
                    st.text(t["summary"][:70])
                    st.caption(f"#{t['ticket_id']} · {t['email']} · {t.get('assignee') or 'Unassigned'}")
                    if st.button("Open", key=f"board_{t['ticket_id']}", icon=":material/open_in_new:", width="stretch"):
                        st.session_state.inbox_ticket = t["ticket_id"]
                        st.rerun()
            if len(in_col) > 12:
                st.caption(f"+{len(in_col) - 12} more. Use List view or filters.")

table = pd.DataFrame(
    {
        "Ticket": [t["ticket_id"] for t in tickets],
        "Customer": [t["email"] for t in tickets],
        "About": [t["summary"][:80] for t in tickets],
        "Priority": [t["priority"] for t in tickets],
        "Status": [LABELS.get(t["status"], t["status"]) for t in tickets],
        "Assigned": [t.get("assignee") or "Unassigned" for t in tickets],
        "Updated": [t["updated_at"].replace("T", " ")[:16] for t in tickets],
    }
)
if browsing and view != "Board":
    st.caption(f"{len(tickets)} ticket(s). Select a row to open it.")
    picked = st.dataframe(
        table, hide_index=True, on_select="rerun", selection_mode="single-row", key="inbox_table",
        alt="Support tickets. Select a row to open it.",
    )
    if picked.selection.rows:
        st.session_state.inbox_ticket = int(table.iloc[picked.selection.rows[0]]["Ticket"])

ticket = desk.get_ticket(user, st.session_state.inbox_ticket) if st.session_state.get("inbox_ticket") else None
if not ticket:
    st.stop()

tid = ticket["ticket_id"]
contact = desk.get_contact(user, ticket["email"])
info, main = st.columns([1, 2.4], gap="medium")

with info, st.container(border=True):  # contact card, like a CRM record's left sidebar
    st.caption("Contact")
    st.markdown(f"**{auth.customer_names().get(ticket['email']) or ticket['email'].split('@')[0].title()}**")
    st.text(ticket["email"])
    if contact:
        st.metric("Lifetime value", f"${contact['lifetime_value']:,.2f}")
        st.metric("Orders", len(contact["orders"]))
        st.metric("Open tickets", sum(1 for t in contact["tickets"] if t["status"] != "resolved"))
        recent = contact["orders"][:3]
        if recent:
            st.caption("Recent orders")
            for o in recent:
                st.text(f"{o['order_id']} · {o['status']}\n{o['item'][:28]} · ${o['total']:,.2f}")
    if st.button("View contact record", key=f"contact_{tid}", icon=":material/person:", width="stretch"):
        st.session_state.contact_email = ticket["email"]
        st.switch_page("app_pages/contacts.py")

with main, st.container(border=True):
    st.subheader(f"Ticket #{tid} · {ticket['email']}")
    st.badge(LABELS[ticket["status"]], color=STATUS_COLOR[ticket["status"]])
    st.caption(
        f"Opened {ticket['created_at'].replace('T', ' ')[:16]} · priority {ticket['priority']} · "
        f"assigned to {ticket.get('assignee') or 'nobody'}"
    )

    with st.chat_message("user"):
        st.caption("Original summary")
        st.text(ticket["summary"])  # plain text: customer-influenced content is never rendered as markdown
    for m in ticket["messages"]:
        with st.chat_message("user" if m["role"] == "customer" else "assistant"):
            st.caption(f"{m['author']} · {m['created_at'].replace('T', ' ')[:16]}")
            st.text(m["body"])

    with st.form(f"staff_reply_{tid}", clear_on_submit=True):
        body = st.text_area("Reply to the customer", max_chars=desk.MAX_MESSAGE)
        send, send_resolve = st.columns(2)
        reply_clicked = send.form_submit_button("Send reply", icon=":material/send:")
        resolve_clicked = send_resolve.form_submit_button("Send and resolve", icon=":material/check_circle:")
        if reply_clicked or resolve_clicked:
            try:
                desk.add_message(user, tid, body)
                if resolve_clicked:
                    desk.update_ticket(user, tid, status="resolved")
            except desk.DeskError as exc:
                st.error(str(exc))
            else:
                st.rerun()

    st.divider()
    with st.form(f"ticket_settings_{tid}"):
        a, b, c = st.columns(3)
        new_status = a.selectbox("Status", desk.STATUSES, index=desk.STATUSES.index(ticket["status"]), format_func=LABELS.get)
        new_priority = b.selectbox("Priority", desk.PRIORITIES, index=desk.PRIORITIES.index(ticket["priority"]))
        options = ["unassigned", *staff]
        current = ticket.get("assignee") if ticket.get("assignee") in staff else "unassigned"
        new_assignee = c.selectbox(
            "Assigned to", options, index=options.index(current), format_func=lambda x: "Unassigned" if x == "unassigned" else x
        )
        if st.form_submit_button("Save changes", icon=":material/save:"):
            try:
                desk.update_ticket(
                    user, tid, status=new_status, priority=new_priority,
                    assignee=None if new_assignee == "unassigned" else new_assignee,
                )
            except desk.DeskError as exc:
                st.error(str(exc))
            else:
                st.toast("Ticket updated")
                st.rerun()
    if ticket.get("assignee") != user["email"] and st.button("Assign to me", icon=":material/person_add:", key=f"mine_{tid}"):
        desk.update_ticket(user, tid, assignee=user["email"])
        st.rerun()
