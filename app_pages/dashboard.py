"""Staff dashboard: ticket volume, response times, satisfaction, refunds and a recent-activity log."""

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

m = desk.metrics(user)


def minutes(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f} min" if value < 120 else f"{value / 60:.1f} h"


def hours(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} h" if value < 48 else f"{value / 24:.1f} days"


with st.container(horizontal=True):
    st.metric("Open", m["by_status"]["open"], border=True)
    st.metric("In progress", m["by_status"]["in_progress"], border=True)
    st.metric("Unassigned", m["unassigned_open"], border=True, help="Tickets that are not resolved and have no owner")
    st.metric("Open > 24h", m["stale_open"], border=True, help="Still 'open' a day after being created")

with st.container(horizontal=True):
    st.metric("First response (avg)", minutes(m["avg_first_response_min"]), border=True)
    st.metric("Time to resolve (avg)", hours(m["avg_resolution_hours"]), border=True)
    st.metric(
        "Satisfaction",
        f"{m['csat_avg']:.1f} / 5" if m["csat_avg"] is not None else "n/a",
        border=True,
        help=f"{m['csat_count']} rating(s) from chats and tickets",
    )
    st.metric("Refunds to review", m["refunds_by_status"]["pending_human_review"][0], border=True)

left, right = st.columns(2)
with left:
    st.subheader("Tickets created per day")
    if m["tickets_per_day"]:
        st.bar_chart(pd.Series(m["tickets_per_day"], name="Tickets"), alt="Tickets created per day, last 14 days with activity")
    else:
        st.caption("No tickets yet.")
with right:
    st.subheader("Open work by priority")
    st.bar_chart(
        pd.Series({k.title(): v for k, v in m["by_priority"].items()}, name="Unresolved tickets"),
        alt="Unresolved tickets by priority",
    )

left, right = st.columns(2)
with left:
    st.subheader("Satisfaction ratings")
    ratings = [r for values in m["csat_by_source"].values() for r in values]
    if ratings:
        counts = pd.Series({f"{n} star{'s' if n > 1 else ''}": ratings.count(n) for n in range(1, 6)}, name="Ratings")
        st.bar_chart(counts, alt="Number of satisfaction ratings by star count")
    else:
        st.caption("No ratings yet. Customers can rate a chat answer or a resolved ticket.")
with right:
    st.subheader("Refunds")
    st.dataframe(
        pd.DataFrame(
            {
                "Status": [s.replace("_", " ").title() for s in m["refunds_by_status"]],
                "Count": [v[0] for v in m["refunds_by_status"].values()],
                "Total ($)": [v[1] for v in m["refunds_by_status"].values()],
            }
        ),
        hide_index=True,
        alt="Refund counts and totals by status",
    )
    st.caption(f"Orders cancelled: {m['cancelled_orders']}")

with st.expander("Recent activity (audit log)"):
    log = desk.recent_audit(user)
    if log:
        st.dataframe(pd.DataFrame(log), hide_index=True, alt="Most recent actions by customers' agents and staff")
    else:
        st.caption("Nothing logged yet.")
