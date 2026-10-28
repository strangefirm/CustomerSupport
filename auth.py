"""Account + session storage for the support desk UI (separate from the shop data in shopease.db).

Passcodes are salted PBKDF2 hashes. Session tokens are random; only their SHA-256 is stored, so a
leaked auth.db cannot be used to hijack a session.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

AUTH_DB = Path(__file__).resolve().parent / "auth.db"
SHOP_DB = Path(__file__).resolve().parent / "shopease.db"
COOKIE = "shopease_session"
SESSION_DAYS = 7
MIN_PASSCODE_LEN = 8
_ITERATIONS = 200_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _hash(passcode: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", passcode.encode(), bytes.fromhex(salt_hex), _ITERATIONS).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _display_name(email: str) -> str:
    return email.split("@")[0].replace(".", " ").title()


def init(seed_users=()) -> None:
    """Create tables and load the pre-provisioned accounts (e.g. from st.secrets) if missing."""
    with closing(_conn()) as c, c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, role TEXT NOT NULL, "
            "salt TEXT NOT NULL, hash TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        c.execute("CREATE TABLE IF NOT EXISTS usage (email TEXT NOT NULL, ts REAL NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS usage_email_ts ON usage (email, ts)")
        for u in seed_users:
            c.execute(
                "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?)",
                (u["email"], _display_name(u["email"]), u["role"], u["salt"], u["hash"], time.time()),
            )


def authenticate(email: str, passcode: str) -> dict | None:
    email = email.strip().lower()
    with closing(_conn()) as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # Hash even for unknown emails so response time doesn't reveal which accounts exist.
    digest = _hash(passcode, row["salt"] if row else "00" * 16)
    if row and hmac.compare_digest(digest, row["hash"]):
        return {"email": row["email"], "name": row["name"], "role": row["role"]}
    return None


def _has_shop_orders(email: str) -> bool:
    if not SHOP_DB.exists():
        return False
    with closing(sqlite3.connect(f"file:{SHOP_DB}?mode=ro", uri=True)) as c:
        return c.execute("SELECT 1 FROM orders WHERE email = ? LIMIT 1", (email,)).fetchone() is not None


def register(name: str, email: str, passcode: str, confirm: str) -> tuple[dict | None, str | None]:
    """Create a customer account. Returns (user, None) or (None, error message)."""
    email = email.strip().lower()
    name = name.strip()
    if not name or not _EMAIL_RE.match(email):
        return None, "Enter your name and a valid email."
    if len(passcode) < MIN_PASSCODE_LEN:
        return None, f"Passcode must be at least {MIN_PASSCODE_LEN} characters."
    if passcode != confirm:
        return None, "Passcodes don't match."

    # We can't verify email ownership, so an address that already has an account or order history
    # can't be claimed here (otherwise anyone could sign up as another customer and read their orders).
    # The same message is used for both cases so the form doesn't reveal which emails are customers.
    taken = "That email can't be used to create an account."
    if _has_shop_orders(email):
        return None, taken

    salt = secrets.token_hex(16)
    try:
        with closing(_conn()) as c, c:
            c.execute(
                "INSERT INTO users VALUES (?, ?, 'customer', ?, ?, ?)",
                (email, name[:60], salt, _hash(passcode, salt), time.time()),
            )
    except sqlite3.IntegrityError:
        return None, taken
    return {"email": email, "name": name[:60], "role": "customer"}, None


def provision(email: str, passcode: str, role: str = "customer") -> bool:
    """Create an account directly (admin use, e.g. for existing customers). False if it already exists."""
    email = email.strip().lower()
    salt = secrets.token_hex(16)
    try:
        with closing(_conn()) as c, c:
            c.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                (email, _display_name(email), role, salt, _hash(passcode, salt), time.time()),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def set_passcode(email: str, passcode: str) -> None:
    """Admin reset: replace an account's passcode and sign out all of its sessions."""
    email = email.strip().lower()
    salt = secrets.token_hex(16)
    with closing(_conn()) as c, c:
        c.execute("UPDATE users SET salt = ?, hash = ? WHERE email = ?", (salt, _hash(passcode, salt), email))
        c.execute("DELETE FROM sessions WHERE email = ?", (email,))


def staff_emails() -> list[str]:
    with closing(_conn()) as c:
        return [r[0] for r in c.execute("SELECT email FROM users WHERE role = 'staff' ORDER BY email")]


def customer_names() -> dict[str, str]:
    """email -> display name for every customer account (staff views only)."""
    with closing(_conn()) as c:
        return {r[0]: r[1] for r in c.execute("SELECT email, name FROM users WHERE role = 'customer'")}


def allow_request(email: str, per_hour: int = 15, per_day: int = 60) -> bool:
    """Count one agent run against the user's quota. False when over limit (each run costs LLM tokens)."""
    now = time.time()
    with closing(_conn()) as c, c:
        c.execute("DELETE FROM usage WHERE ts < ?", (now - 86400,))
        hour, day = c.execute(
            "SELECT SUM(ts > ?), COUNT(*) FROM usage WHERE email = ?", (now - 3600, email)
        ).fetchone()
        if (hour or 0) >= per_hour or day >= per_day:
            return False
        c.execute("INSERT INTO usage VALUES (?, ?)", (email, now))
    return True


def create_session(email: str) -> str:
    token = secrets.token_urlsafe(32)
    with closing(_conn()) as c, c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        c.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (_token_hash(token), email, time.time() + SESSION_DAYS * 86400),
        )
    return token


def user_for_token(token: str) -> dict | None:
    with closing(_conn()) as c:
        row = c.execute(
            "SELECT u.email, u.name, u.role FROM sessions s JOIN users u ON u.email = s.email "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_token_hash(token), time.time()),
        ).fetchone()
    return dict(row) if row else None


def revoke(token: str) -> None:
    with closing(_conn()) as c, c:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
