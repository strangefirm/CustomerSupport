"""Customer view: own tickets (with replies and a rating once resolved) and own refunds."""

import pandas as pd
import streamlit as st

import desk

user = st.session_state.user

if not desk.db_ready():
    st.info("The shop database hasn't been created yet. Run `python mcp_server.py` once, then refresh.", title="No data yet")
    st.stop()

LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "pending_human_review": "Under review",
    "approved": "Approved",
    "rejected": "Rejected",
}
STATUS_COLOR = {"open": "orange", "in_progress": "blue", "resolved": "green"}

tickets_tab, refunds_tab = st.tabs(["Tickets", "Refunds"])

with tickets_tab:
    tickets = desk.list_tickets(user)
    if not tickets:
        st.info(
            "When the assistant can't solve something on its own, it opens a ticket and you can follow it here.",
            title="No tickets yet",
        )
    else:
        table = pd.DataFrame(
            {
                "Ticket": [t["ticket_id"] for t in tickets],
                "About": [t["summary"][:70] for t in tickets],
                "Priority": [t["priority"].title() for t in tickets],
                "Status": [LABELS.get(t["status"], t["status"]) for t in tickets],
                "Updated": [t["updated_at"].replace("T", " ")[:16] for t in tickets],
            }
        )
        picked = st.dataframe(
            table, hide_index=True, on_select="rerun", selection_mode="single-row", key="my_tickets_table",
            alt="Your support tickets. Select a row to open it.",
        )
        if picked.selection.rows:
            st.session_state.my_ticket = int(table.iloc[picked.selection.rows[0]]["Ticket"])

        ticket = desk.get_ticket(user, st.session_state.my_ticket) if st.session_state.get("my_ticket") else None
        if ticket:
            with st.container(border=True):
                st.subheader(f"Ticket #{ticket['ticket_id']}")
                st.badge(LABELS[ticket["status"]], color=STATUS_COLOR.get(ticket["status"], "gray"))
                st.caption(f"Opened {ticket['created_at'].replace('T', ' ')[:16]} · priority {ticket['priority']}")
                with st.chat_message("user"):
                    st.caption("Your request")
                    st.text(ticket["summary"])  # plain text: never render stored text as markdown
                for m in ticket["messages"]:
                    with st.chat_message("user" if m["role"] == "customer" else "assistant"):
                        st.caption(f"{m['author']} · {m['created_at'].replace('T', ' ')[:16]}")
                        st.text(m["body"])

                if ticket["status"] == "resolved" and ticket["rating"] is None:
                    st.caption("This ticket is resolved. How did we do?")
                    stars = st.feedback("stars", key=f"ticket_rating_{ticket['ticket_id']}")
                    if stars is not None:
                        desk.submit_csat(user, "ticket", str(ticket["ticket_id"]), stars + 1)
                        st.toast("Thanks for your feedback!")
                        st.rerun()
                elif ticket["rating"] is not None:
                    st.caption(f"You rated this {ticket['rating']}/5. Thank you!")

                with st.form(f"reply_{ticket['ticket_id']}", clear_on_submit=True):
                    body = st.text_area(
                        "Reply" if ticket["status"] != "resolved" else "Reply (this will reopen the ticket)",
                        max_chars=desk.MAX_MESSAGE,
                    )
                    if st.form_submit_button("Send reply", icon=":material/send:"):
                        try:
                            desk.add_message(user, ticket["ticket_id"], body)
                        except desk.DeskError as exc:
                            st.error(str(exc))
                        else:
                            st.rerun()
        elif st.session_state.get("my_ticket"):
            st.session_state.pop("my_ticket")

with refunds_tab:
    refunds = desk.list_refunds(user)
    if not refunds:
        st.info("Refund requests you make through the chat appear here.", title="No refunds yet")
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "Refund": [r["refund_id"] for r in refunds],
                    "Order": [r["order_id"] for r in refunds],
                    "Item": [r["item"] for r in refunds],
                    "Amount": [f"${r['amount']:,.2f}" for r in refunds],
                    "Status": [LABELS.get(r["status"], r["status"]) for r in refunds],
                    "Requested": [r["created_at"].replace("T", " ")[:16] for r in refunds],
                }
            ),
            hide_index=True,
            alt="Your refund requests and their status",
        )
        st.caption("Refunds above $200 are reviewed by a person, which can take up to a day.")
