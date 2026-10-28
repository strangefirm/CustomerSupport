"""Support-desk data layer: tickets, replies, refund approvals, satisfaction ratings, chat history, metrics.

Every function takes the signed-in `actor` ({"email", "role"}) and enforces permissions itself, so a
UI bug cannot leak data: customers only ever reach their own records, and only staff can change ticket
status, assign tickets or decide refunds. All SQL is parameterised. Free text is stripped of control
characters and length-capped before it is stored.
"""

import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import auth

DB_PATH = Path(os.getenv("SHOPEASE_DB") or Path(__file__).resolve().parent / "shopease.db")

STATUSES = ("open", "in_progress", "resolved")
PRIORITIES = ("low", "medium", "high")
MAX_MESSAGE = 1000
MAX_MESSAGES_PER_HOUR = 20
HISTORY_DAYS = 30
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]+")  # keeps \t and \n
_UNSET = object()


class DeskError(Exception):
    """A problem that is safe to show to the user."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(text: str, limit: int = MAX_MESSAGE) -> str:
    return _CONTROL.sub(" ", str(text)).strip()[:limit]


def db_ready() -> bool:
    return DB_PATH.exists()


def _conn() -> sqlite3.Connection:
    # Never create the file here: an empty shopease.db would stop the MCP server from seeding its tables.
    if not DB_PATH.exists():
        raise DeskError("The shop database has not been created yet. Run `python mcp_server.py` once, then refresh.")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def is_staff(actor: dict) -> bool:
    return actor.get("role") == "staff"


def _require_staff(actor: dict) -> None:
    if not is_staff(actor):
        raise DeskError("This action is only available to support staff.")


def _audit(conn: sqlite3.Connection, actor: dict, action: str, detail: str) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, tool TEXT, args TEXT, outcome TEXT)"
    )
    conn.execute(
        "INSERT INTO audit (ts, actor, tool, args, outcome) VALUES (?,?,?,?,?)",
        (_now(), actor["email"], f"desk.{action}", detail[:1000], "ok"),
    )


# ------------------------------------------------------------------ schema
def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if exists and column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure_schema() -> None:
    """Idempotent: add the columns/tables the desk needs on top of the MCP server's base tables."""
    with closing(_conn()) as conn, conn:
        for col in ("assignee TEXT", "updated_at TEXT", "first_response_at TEXT", "resolved_at TEXT"):
            _add_column(conn, "tickets", col.split()[0], col.split()[1])
        for col in ("decided_by TEXT", "decided_at TEXT", "decision_note TEXT"):
            _add_column(conn, "refunds", col.split()[0], col.split()[1])
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
                author_email TEXT NOT NULL, author_role TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ticket_messages_ticket ON ticket_messages (ticket_id);
            CREATE TABLE IF NOT EXISTS csat (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, source TEXT NOT NULL, ref TEXT NOT NULL,
                rating INTEGER NOT NULL, comment TEXT, created_at TEXT NOT NULL, UNIQUE (email, source, ref));
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, kind TEXT NOT NULL,
                source TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS chat_history_email ON chat_history (email, id);
            """
        )


# ------------------------------------------------------------------ tickets
def _ticket_dict(row: sqlite3.Row, actor: dict) -> dict:
    d = dict(row)
    d["updated_at"] = d.get("updated_at") or d["created_at"]
    if not is_staff(actor):
        d.pop("assignee", None)  # customers never see which agent it is
        d.pop("first_response_at", None)
    return d


def list_tickets(actor: dict, status: str | None = None, priority: str | None = None,
                 assignee: str | None = None, search: str | None = None, limit: int = 300) -> list[dict]:
    sql = ["SELECT * FROM tickets WHERE 1=1"]
    params: list = []
    if not is_staff(actor):
        sql.append("AND email = ?")
        params.append(actor["email"])
    if status in STATUSES:
        sql.append("AND status = ?")
        params.append(status)
    if priority in PRIORITIES:
        sql.append("AND priority = ?")
        params.append(priority)
    if is_staff(actor) and assignee:
        if assignee == "unassigned":
            sql.append("AND assignee IS NULL")
        else:
            sql.append("AND assignee = ?")
            params.append(assignee)
    if search:
        sql.append("AND (summary LIKE ? OR email LIKE ? OR CAST(ticket_id AS TEXT) = ?)")
        like = f"%{_clean(search, 80)}%"
        params += [like, like, _clean(search, 20)]
    sql.append("ORDER BY CASE status WHEN 'resolved' THEN 1 ELSE 0 END, COALESCE(updated_at, created_at) DESC LIMIT ?")
    params.append(max(1, min(limit, 1000)))
    with closing(_conn()) as conn:
        return [_ticket_dict(r, actor) for r in conn.execute(" ".join(sql), params)]


def get_ticket(actor: dict, ticket_id: int) -> dict | None:
    """The ticket plus its message thread, or None (also when it belongs to someone else)."""
    with closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (int(ticket_id),)).fetchone()
        if not row or (not is_staff(actor) and row["email"] != actor["email"]):
            return None
        ticket = _ticket_dict(row, actor)
        thread = []
        for m in conn.execute("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id", (row["ticket_id"],)):
            if is_staff(actor):
                who = m["author_email"]
            else:
                who = "You" if m["author_role"] == "customer" else "Support team"
            thread.append({"author": who, "role": m["author_role"], "body": m["body"], "created_at": m["created_at"]})
        ticket["messages"] = thread
        ticket["rating"] = conn.execute(
            "SELECT rating FROM csat WHERE email = ? AND source = 'ticket' AND ref = ?", (row["email"], str(row["ticket_id"]))
        ).fetchone()
        ticket["rating"] = ticket["rating"][0] if ticket["rating"] else None
        return ticket


def add_message(actor: dict, ticket_id: int, body: str) -> None:
    body = _clean(body)
    if not body:
        raise DeskError("Write a message first.")
    now = _now()
    with closing(_conn()) as conn, conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (int(ticket_id),)).fetchone()
        if not ticket or (not is_staff(actor) and ticket["email"] != actor["email"]):
            raise DeskError("Ticket not found.")
        since = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        recent = conn.execute(
            "SELECT COUNT(*) FROM ticket_messages WHERE author_email = ? AND created_at >= ?", (actor["email"], since)
        ).fetchone()[0]
        if recent >= MAX_MESSAGES_PER_HOUR:
            raise DeskError("You're sending messages too quickly. Please wait a little.")
        role = "staff" if is_staff(actor) else "customer"
        conn.execute(
            "INSERT INTO ticket_messages (ticket_id, author_email, author_role, body, created_at) VALUES (?,?,?,?,?)",
            (ticket["ticket_id"], actor["email"], role, body, now),
        )
        if role == "staff":
            # First staff reply starts the response clock; an untouched ticket becomes in progress.
            conn.execute(
                "UPDATE tickets SET updated_at = ?, first_response_at = COALESCE(first_response_at, ?), "
                "status = CASE WHEN status = 'open' THEN 'in_progress' ELSE status END WHERE ticket_id = ?",
                (now, now, ticket["ticket_id"]),
            )
        else:  # a customer reply reopens a resolved ticket
            conn.execute(
                "UPDATE tickets SET updated_at = ?, status = CASE WHEN status = 'resolved' THEN 'open' ELSE status END, "
                "resolved_at = CASE WHEN status = 'resolved' THEN NULL ELSE resolved_at END WHERE ticket_id = ?",
                (now, ticket["ticket_id"]),
            )
        _audit(conn, actor, "ticket_reply", f"ticket={ticket['ticket_id']} role={role}")


def update_ticket(actor: dict, ticket_id: int, status: str | None = None, priority: str | None = None,
                  assignee=_UNSET) -> None:
    _require_staff(actor)
    if status is not None and status not in STATUSES:
        raise DeskError("Unknown status.")
    if priority is not None and priority not in PRIORITIES:
        raise DeskError("Unknown priority.")
    if assignee is not _UNSET and assignee is not None and assignee not in auth.staff_emails():
        raise DeskError("Tickets can only be assigned to staff members.")
    now = _now()
    with closing(_conn()) as conn, conn:
        if not conn.execute("SELECT 1 FROM tickets WHERE ticket_id = ?", (int(ticket_id),)).fetchone():
            raise DeskError("Ticket not found.")
        if status is not None:
            conn.execute(
                "UPDATE tickets SET status = ?, resolved_at = CASE WHEN ? = 'resolved' THEN COALESCE(resolved_at, ?) ELSE NULL END "
                "WHERE ticket_id = ?",
                (status, status, now, int(ticket_id)),
            )
        if priority is not None:
            conn.execute("UPDATE tickets SET priority = ? WHERE ticket_id = ?", (priority, int(ticket_id)))
        if assignee is not _UNSET:
            conn.execute("UPDATE tickets SET assignee = ? WHERE ticket_id = ?", (assignee, int(ticket_id)))
        conn.execute("UPDATE tickets SET updated_at = ? WHERE ticket_id = ?", (now, int(ticket_id)))
        _audit(conn, actor, "ticket_update", f"ticket={ticket_id} status={status} priority={priority} assignee={None if assignee is _UNSET else assignee}")


# ------------------------------------------------------------------ contacts (staff CRM view)
def list_contacts(actor: dict, search: str | None = None, limit: int = 500) -> list[dict]:
    """One row per customer email with order / ticket / refund totals."""
    _require_staff(actor)
    sql = """
        SELECT e.email,
               (SELECT COUNT(*) FROM orders o WHERE o.email = e.email) AS orders,
               (SELECT COALESCE(SUM(total), 0) FROM orders o WHERE o.email = e.email AND o.status != 'cancelled') AS lifetime_value,
               (SELECT COUNT(*) FROM tickets t WHERE t.email = e.email AND t.status != 'resolved') AS open_tickets,
               (SELECT COUNT(*) FROM tickets t WHERE t.email = e.email) AS tickets,
               (SELECT MAX(placed_at) FROM orders o WHERE o.email = e.email) AS last_order
        FROM (SELECT email FROM orders UNION SELECT email FROM tickets) e
        WHERE e.email LIKE ? ORDER BY lifetime_value DESC LIMIT ?
    """
    like = f"%{_clean(search or '', 80)}%"
    with closing(_conn()) as conn:
        return [dict(r) for r in conn.execute(sql, (like, max(1, min(limit, 1000))))]


def get_contact(actor: dict, email: str) -> dict | None:
    """Everything known about one customer: orders, tickets, refunds, satisfaction ratings."""
    _require_staff(actor)
    email = _clean(email, 120).lower()
    with closing(_conn()) as conn:
        orders = [
            dict(r)
            for r in conn.execute(
                "SELECT o.order_id, COALESCE(i.name, o.sku) AS item, o.qty, o.total, o.status, o.placed_at, o.delivered_at "
                "FROM orders o LEFT JOIN inventory i ON i.sku = o.sku WHERE o.email = ? ORDER BY o.placed_at DESC",
                (email,),
            )
        ]
        tickets = [dict(r) for r in conn.execute("SELECT * FROM tickets WHERE email = ? ORDER BY ticket_id DESC", (email,))]
        if not orders and not tickets:
            return None
        refunds = [
            dict(r)
            for r in conn.execute(
                "SELECT r.refund_id, r.order_id, r.amount, r.status, r.reason, r.created_at FROM refunds r "
                "JOIN orders o ON o.order_id = r.order_id WHERE o.email = ? ORDER BY r.refund_id DESC",
                (email,),
            )
        ]
        csat = [r[0] for r in conn.execute("SELECT rating FROM csat WHERE email = ?", (email,))]
    return {
        "email": email,
        "orders": orders,
        "tickets": tickets,
        "refunds": refunds,
        "lifetime_value": sum(o["total"] for o in orders if o["status"] != "cancelled"),
        "csat_avg": sum(csat) / len(csat) if csat else None,
    }


# ------------------------------------------------------------------ refunds
def list_refunds(actor: dict, status: str | None = None) -> list[dict]:
    sql = [
        "SELECT r.refund_id, r.order_id, r.amount, r.reason, r.status, r.created_at, r.decided_by, r.decided_at, r.decision_note, "
        "o.email, o.total, o.delivered_at, COALESCE(i.name, o.sku) AS item "
        "FROM refunds r JOIN orders o ON o.order_id = r.order_id LEFT JOIN inventory i ON i.sku = o.sku WHERE 1=1"
    ]
    params: list = []
    if not is_staff(actor):
        sql.append("AND o.email = ?")
        params.append(actor["email"])
    if status:
        sql.append("AND r.status = ?")
        params.append(status)
    sql.append("ORDER BY r.refund_id DESC LIMIT 500")
    with closing(_conn()) as conn:
        rows = [dict(r) for r in conn.execute(" ".join(sql), params)]
    if not is_staff(actor):
        for r in rows:
            for key in ("email", "decided_by", "decision_note"):
                r.pop(key, None)
    return rows


def decide_refund(actor: dict, refund_id: int, approve: bool, note: str = "") -> None:
    """Approve or reject a refund waiting for human review. Only the first decision wins."""
    _require_staff(actor)
    note = _clean(note, 300)
    if not approve and not note:
        raise DeskError("Add a short reason when rejecting a refund.")
    new_status = "approved" if approve else "rejected"
    with closing(_conn()) as conn, conn:
        cur = conn.execute(
            "UPDATE refunds SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? "
            "WHERE refund_id = ? AND status = 'pending_human_review'",
            (new_status, actor["email"], _now(), note or None, int(refund_id)),
        )
        if cur.rowcount != 1:
            raise DeskError("That refund is no longer waiting for review.")
        _audit(conn, actor, "refund_decision", f"refund={refund_id} -> {new_status}")


# --------------------------------------------------------------------- csat
def submit_csat(actor: dict, source: str, ref: str, rating: int, comment: str = "") -> bool:
    """Store a 1-5 rating. Returns False if this exact thing was already rated."""
    if source not in {"chat", "ticket"} or not 1 <= int(rating) <= 5:
        raise DeskError("Invalid rating.")
    ref = _clean(ref, 40)
    with closing(_conn()) as conn, conn:
        if source == "ticket":
            ticket = conn.execute("SELECT email, status FROM tickets WHERE ticket_id = ?", (int(ref),)).fetchone()
            if not ticket or ticket["email"] != actor["email"]:
                raise DeskError("Ticket not found.")
            if ticket["status"] != "resolved":
                raise DeskError("You can rate a ticket once it is resolved.")
        elif not conn.execute("SELECT 1 FROM chat_history WHERE id = ? AND email = ?", (int(ref), actor["email"])).fetchone():
            raise DeskError("Conversation not found.")
        try:
            conn.execute(
                "INSERT INTO csat (email, source, ref, rating, comment, created_at) VALUES (?,?,?,?,?,?)",
                (actor["email"], source, ref, int(rating), _clean(comment, 300) or None, _now()),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def has_rated(actor: dict, source: str, ref: str) -> bool:
    with closing(_conn()) as conn:
        return conn.execute(
            "SELECT 1 FROM csat WHERE email = ? AND source = ? AND ref = ?", (actor["email"], source, str(ref))
        ).fetchone() is not None


# ------------------------------------------------------------- chat history
def save_event(actor: dict, kind: str, source: str, text: str) -> int:
    with closing(_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO chat_history (email, kind, source, text, created_at) VALUES (?,?,?,?,?)",
            (actor["email"], kind, _clean(source, 40), _clean(text, 4000), _now()),
        )
        return cur.lastrowid


def load_history(actor: dict, limit: int = 200) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
    with closing(_conn()) as conn, conn:
        conn.execute("DELETE FROM chat_history WHERE created_at < ?", (cutoff,))  # retention
        rows = conn.execute(
            "SELECT id, kind, source, text FROM chat_history WHERE email = ? ORDER BY id DESC LIMIT ?",
            (actor["email"], max(1, min(limit, 500))),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_history(actor: dict) -> None:
    with closing(_conn()) as conn, conn:
        conn.execute("DELETE FROM chat_history WHERE email = ?", (actor["email"],))


def recent_context(actor: dict, turns: int = 3) -> str:
    """A short transcript of the customer's last few exchanges, to give the agents some memory."""
    with closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT kind, text FROM chat_history WHERE email = ? AND kind IN ('user', 'agent') ORDER BY id DESC LIMIT ?",
            (actor["email"], turns * 4),
        ).fetchall()
    lines, users = [], 0
    for r in rows:  # newest first; stop after `turns` customer messages
        if r["kind"] == "user":
            users += 1
            if users > turns:
                break
        lines.append(f"{'Customer' if r['kind'] == 'user' else 'Support'}: {_clean(r['text'], 300)}")
    return "\n".join(reversed(lines))


# ------------------------------------------------------------------ metrics
def _minutes_between(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60


def metrics(actor: dict) -> dict:
    _require_staff(actor)
    with closing(_conn()) as conn:
        tickets = [dict(r) for r in conn.execute("SELECT * FROM tickets")]
        csat = [dict(r) for r in conn.execute("SELECT source, rating FROM csat")]
        refunds = [dict(r) for r in conn.execute("SELECT status, amount FROM refunds")]
        cancelled = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'cancelled'").fetchone()[0]
    first_resp = [_minutes_between(t["created_at"], t["first_response_at"]) for t in tickets if t.get("first_response_at")]
    resolution = [_minutes_between(t["created_at"], t["resolved_at"]) / 60 for t in tickets if t.get("resolved_at")]
    day_ago = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    by_day: dict[str, int] = {}
    for t in tickets:
        day = t["created_at"][:10]
        by_day[day] = by_day.get(day, 0) + 1
    ratings = [c["rating"] for c in csat]
    return {
        "by_status": {s: sum(1 for t in tickets if t["status"] == s) for s in STATUSES},
        "by_priority": {p: sum(1 for t in tickets if t["priority"] == p and t["status"] != "resolved") for p in PRIORITIES},
        "unassigned_open": sum(1 for t in tickets if t["status"] != "resolved" and not t.get("assignee")),
        "stale_open": sum(1 for t in tickets if t["status"] == "open" and t["created_at"] < day_ago),
        "avg_first_response_min": sum(first_resp) / len(first_resp) if first_resp else None,
        "avg_resolution_hours": sum(resolution) / len(resolution) if resolution else None,
        "csat_avg": sum(ratings) / len(ratings) if ratings else None,
        "csat_count": len(ratings),
        "csat_by_source": {s: [c["rating"] for c in csat if c["source"] == s] for s in ("chat", "ticket")},
        "refunds_by_status": {
            s: (sum(1 for r in refunds if r["status"] == s), round(sum(r["amount"] for r in refunds if r["status"] == s), 2))
            for s in ("approved", "pending_human_review", "rejected")
        },
        "cancelled_orders": cancelled,
        "tickets_per_day": dict(sorted(by_day.items())[-14:]),
    }


def recent_audit(actor: dict, limit: int = 100) -> list[dict]:
    _require_staff(actor)
    with closing(_conn()) as conn:
        try:
            return [dict(r) for r in conn.execute("SELECT ts, actor, tool, outcome FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
        except sqlite3.OperationalError:  # no audit table yet
            return []
