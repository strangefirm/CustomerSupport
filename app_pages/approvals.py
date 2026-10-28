"""Staff refund approval queue: refunds above the auto-approval limit wait here for a human decision."""

import pandas as pd
import streamlit as st

import desk

user = st.session_state.user
if not desk.is_staff(user):
    st.error("This page is only available to support staff.")
    st.stop()
if not desk.db_ready():
    st.info("The shop database hasn't been created yet. Run `python mcp_server.py` once, then refresh.", title="No data yet")
    st.stop()

pending = desk.list_refunds(user, "pending_human_review")
st.caption("Refunds above $200 are held for review. Approving here records you as the decision-maker.")

if not pending:
    st.success("Nothing is waiting for review.", title="All caught up")
else:
    for r in reversed(pending):  # oldest first
        with st.container(border=True):
            top, amount = st.columns([3, 1])
            top.markdown(f"**Refund #{r['refund_id']}** · order {r['order_id']} · {r['email']}")
            top.caption(
                f"{r['item']} · delivered {(r['delivered_at'] or '')[:10] or 'n/a'} · requested {r['created_at'].replace('T', ' ')[:16]}"
            )
            amount.metric("Amount", f"${r['amount']:,.2f}", delta=None if r["amount"] == r["total"] else f"of ${r['total']:,.2f}", delta_color="off")
            st.text(f"Reason: {r['reason']}")  # plain text: the reason comes from the customer/agent
            note = st.text_input("Decision note (required to reject)", key=f"note_{r['refund_id']}", max_chars=300)
            approve_col, reject_col = st.columns(2)
            decision = None
            if approve_col.button("Approve refund", key=f"approve_{r['refund_id']}", icon=":material/check:", type="primary", width="stretch"):
                decision = True
            if reject_col.button("Reject refund", key=f"reject_{r['refund_id']}", icon=":material/close:", width="stretch"):
                decision = False
            if decision is not None:
                try:
                    desk.decide_refund(user, r["refund_id"], decision, note)
                except desk.DeskError as exc:
                    st.error(str(exc))
                else:
                    st.toast(f"Refund #{r['refund_id']} {'approved' if decision else 'rejected'}")
                    st.rerun()

decided = [r for r in desk.list_refunds(user) if r["decided_at"]][:15]
if decided:
    st.subheader("Recent decisions")
    st.dataframe(
        pd.DataFrame(
            {
                "Refund": [r["refund_id"] for r in decided],
                "Order": [r["order_id"] for r in decided],
                "Customer": [r["email"] for r in decided],
                "Amount": [f"${r['amount']:,.2f}" for r in decided],
                "Decision": [r["status"].title() for r in decided],
                "By": [r["decided_by"] for r in decided],
                "When": [r["decided_at"].replace("T", " ")[:16] for r in decided],
                "Note": [r["decision_note"] or "" for r in decided],
            }
        ),
        hide_index=True,
        alt="Recent refund decisions",
    )
