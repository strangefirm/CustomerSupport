"""ShopEase support desk: sign-in, then role-based pages.

    streamlit run streamlit_app.py

Customers get the AI support chat and a view of their own tickets and refunds. Staff also get a
ticket inbox, a refund approval queue and a dashboard. Accounts and sessions live in auth.db (see
auth.py), the support-desk data layer is desk.py, and the pre-provisioned demo accounts come from
.streamlit/secrets.toml.
"""

import streamlit as st

import auth
import desk
import ui

MAX_LOGIN_ATTEMPTS = 5
MAX_SIGNUPS_PER_SESSION = 3

st.set_page_config(page_title="ShopEase support desk", page_icon=":material/support_agent:")
ui.apply()  # shared 3D look: raised cards, gradient buttons, dark sidebar (runs on the sign-in screen too)


# ---------- authentication ----------

auth.init(st.secrets.get("users", []))


def set_cookie(token: str, max_age: int) -> None:
    """Remember the session in the browser so a refresh doesn't sign the user out."""
    st.html(
        f"<script>document.cookie = '{auth.COOKIE}={token}; max-age={max_age}; path=/; SameSite=Strict';</script>",
        unsafe_allow_javascript=True,
    )


def start_session(user: dict) -> None:
    token = auth.create_session(user["email"])
    st.session_state.clear()
    st.session_state.update(user=user, token=token, new_cookie=token)
    st.rerun()


def sign_in_screen() -> None:
    st.title("ShopEase support desk")
    if st.session_state.pop("clear_cookie", False):
        set_cookie("", 0)

    sign_in_tab, sign_up_tab = st.tabs(["Sign in", "Create account"])

    with sign_in_tab:
        if st.session_state.get("failed_logins", 0) >= MAX_LOGIN_ATTEMPTS:
            st.error("Too many failed attempts. Reload the page to try again later.", title="Locked")
        else:
            with st.form("sign_in"):
                email = st.text_input("Email", key="signin_email")
                passcode = st.text_input("Passcode", type="password", key="signin_pw")
                submitted = st.form_submit_button("Sign in", type="primary")
            if submitted:
                user = auth.authenticate(email, passcode)
                if user:
                    start_session(user)
                st.session_state.failed_logins = st.session_state.get("failed_logins", 0) + 1
                st.error("Invalid email or passcode.")

    with sign_up_tab:
        if st.session_state.get("signups", 0) >= MAX_SIGNUPS_PER_SESSION:
            st.error("Too many sign-up attempts. Reload the page to try again later.", title="Locked")
        else:
            with st.form("sign_up"):
                name = st.text_input("Your name", key="signup_name")
                email = st.text_input("Email", key="signup_email")
                passcode = st.text_input(f"Passcode (at least {auth.MIN_PASSCODE_LEN} characters)", type="password", key="signup_pw")
                confirm = st.text_input("Confirm passcode", type="password", key="signup_confirm")
                submitted = st.form_submit_button("Create account", type="primary")
            if submitted:
                user, error = auth.register(name, email, passcode, confirm)
                if user:
                    start_session(user)
                st.session_state.signups = st.session_state.get("signups", 0) + 1
                st.error(error)
    st.stop()


if "user" not in st.session_state:
    cookie_token = st.context.cookies.get(auth.COOKIE)
    restored = auth.user_for_token(cookie_token) if isinstance(cookie_token, str) and cookie_token else None
    if restored:  # page refresh / returning visitor with a valid session
        st.session_state.update(user=restored, token=cookie_token)
    else:
        sign_in_screen()

if new_cookie := st.session_state.pop("new_cookie", None):
    set_cookie(new_cookie, auth.SESSION_DAYS * 86400)

user = st.session_state.user
is_staff = user["role"] == "staff"


# ---------- pages for this role ----------

if desk.db_ready():
    @st.cache_resource
    def prepare_database() -> bool:
        desk.ensure_schema()  # adds the ticket/refund columns and desk tables once per server run
        return True

    prepare_database()

if is_staff:
    pending = len(desk.list_refunds(user, "pending_human_review")) if desk.db_ready() else 0
    pages = {
        "Service": [
            st.Page("app_pages/inbox.py", title="Ticket inbox", icon=":material/inbox:", url_path="inbox", default=True),
            st.Page(
                "app_pages/approvals.py",
                title=f"Refund approvals ({pending})" if pending else "Refund approvals",
                icon=":material/fact_check:",
                url_path="approvals",
            ),
        ],
        "CRM": [st.Page("app_pages/contacts.py", title="Contacts", icon=":material/contacts:", url_path="contacts")],
        "Reports": [st.Page("app_pages/dashboard.py", title="Dashboard", icon=":material/monitoring:", url_path="dashboard")],
        "Tools": [st.Page("app_pages/chat.py", title="Support chat", icon=":material/chat:", url_path="chat")],
    }
else:
    pages = [
        st.Page("app_pages/chat.py", title="Support chat", icon=":material/chat:", url_path="chat", default=True),
        st.Page("app_pages/my_tickets.py", title="My tickets and refunds", icon=":material/confirmation_number:", url_path="my-requests"),
    ]

page = st.navigation(pages)

with st.sidebar:
    st.caption(f"Signed in as {user['name'] or user['email']}" + (" (staff)" if is_staff else ""))
    if st.button("Sign out", icon=":material/logout:", width="stretch"):
        auth.revoke(st.session_state.token)
        st.session_state.clear()
        st.session_state.clear_cookie = True
        st.rerun()

st.title(page.title, icon=page.icon)
page.run()
