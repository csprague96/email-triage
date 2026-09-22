"""SQLite storage for processed emails, assessments, drafts, and user corrections."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_FILE, ensure_dirs
from .models import Assessment, DraftResult, Email

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    subject TEXT, sender_name TEXT, sender_email TEXT,
    to_json TEXT, cc_json TEXT, received TEXT, body_text TEXT,
    has_attachments INTEGER, conversation_id TEXT, is_read INTEGER, web_link TEXT,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS assessments (
    email_id TEXT PRIMARY KEY REFERENCES emails(id),
    priority TEXT, confidence REAL, probabilities TEXT, signals TEXT,
    time_sensitivity REAL, tags TEXT, tag_scores TEXT, jev_priority TEXT,
    adjusted_by_rule TEXT, needs_review INTEGER, model TEXT, input_tokens INTEGER,
    user_priority TEXT, user_tags TEXT, assessed_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT REFERENCES emails(id),
    body_text TEXT, attempts INTEGER, checks TEXT, passed INTEGER, notes TEXT,
    outlook_draft_id TEXT, status TEXT DEFAULT 'ready', created_at TEXT
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT, subject TEXT, sender_email TEXT, snippet TEXT,
    from_priority TEXT, to_priority TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received DESC);
CREATE INDEX IF NOT EXISTS idx_assess_priority ON assessments(priority);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            ensure_dirs()
            _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.commit()
        return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """Additive schema changes for databases created by earlier versions."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(assessments)").fetchall()}
    if "thread_json" not in cols:
        c.execute("ALTER TABLE assessments ADD COLUMN thread_json TEXT")
    if "handled_at" not in cols:
        c.execute("ALTER TABLE assessments ADD COLUMN handled_at TEXT")


def get_meta(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with _lock:
        conn().execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        conn().commit()


def is_processed(email_id: str) -> bool:
    with _lock:
        return conn().execute("SELECT 1 FROM assessments WHERE email_id=?", (email_id,)).fetchone() is not None


def save_email(email: Email) -> None:
    with _lock:
        conn().execute(
            """INSERT OR REPLACE INTO emails(id,subject,sender_name,sender_email,to_json,cc_json,received,body_text,
               has_attachments,conversation_id,is_read,web_link,processed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                email.id, email.subject, email.sender_name, email.sender_email,
                json.dumps(email.to), json.dumps(email.cc), email.received.isoformat(), email.body_text,
                int(email.has_attachments), email.conversation_id, int(email.is_read), email.web_link, _now(),
            ),
        )
        conn().commit()


def save_assessment(email_id: str, a: Assessment) -> None:
    thread_json = json.dumps(a.thread.to_dict()) if a.thread else None
    with _lock:
        conn().execute(
            """INSERT OR REPLACE INTO assessments(email_id,priority,confidence,probabilities,signals,time_sensitivity,
               tags,tag_scores,jev_priority,adjusted_by_rule,needs_review,model,input_tokens,user_priority,user_tags,assessed_at,thread_json,handled_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,
                 (SELECT user_priority FROM assessments WHERE email_id=?),
                 (SELECT user_tags FROM assessments WHERE email_id=?), ?, ?,
                 (SELECT handled_at FROM assessments WHERE email_id=?))""",
            (
                email_id, a.priority, a.confidence, json.dumps(a.probabilities), json.dumps(a.signals),
                a.time_sensitivity, json.dumps(a.tags), json.dumps(a.tag_scores), a.jev_priority,
                a.adjusted_by_rule, int(a.needs_review), a.model, a.input_tokens, email_id, email_id, _now(), thread_json, email_id,
            ),
        )
        conn().commit()


def update_thread(email_id: str, thread: dict) -> None:
    with _lock:
        conn().execute("UPDATE assessments SET thread_json=? WHERE email_id=?", (json.dumps(thread), email_id))
        conn().commit()


def save_draft(email_id: str, d: DraftResult) -> int:
    with _lock:
        cur = conn().execute(
            """INSERT INTO drafts(email_id,body_text,attempts,checks,passed,notes,outlook_draft_id,status,created_at)
               VALUES(?,?,?,?,?,?,?,'ready',?)""",
            (email_id, d.body_text, d.attempts, json.dumps(d.checks), int(d.passed), d.notes, d.outlook_draft_id, _now()),
        )
        conn().commit()
        return int(cur.lastrowid)


def set_draft_status(draft_id: int, status: str) -> None:
    with _lock:
        conn().execute("UPDATE drafts SET status=? WHERE id=?", (status, draft_id))
        conn().commit()


def ready_drafts_in_conversation(conversation_id: str, exclude_email_id: str = "") -> list[dict]:
    if not conversation_id:
        return []
    with _lock:
        rows = conn().execute(
            """SELECT d.* FROM drafts d JOIN emails e ON e.id=d.email_id
               WHERE d.status='ready' AND e.conversation_id=? AND e.id<>?""",
            (conversation_id, exclude_email_id),
        ).fetchall()
    return [_draft_row(r) for r in rows]


def list_ready_drafts_with_conversation(limit: int = 50) -> list[dict]:
    with _lock:
        rows = conn().execute(
            """SELECT d.id, d.email_id, d.created_at, e.conversation_id FROM drafts d JOIN emails e ON e.id=d.email_id
               WHERE d.status='ready' ORDER BY d.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_draft(draft_id: int) -> dict | None:
    with _lock:
        row = conn().execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    return _draft_row(row) if row else None


def drafts_for_email(email_id: str) -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT * FROM drafts WHERE email_id=? ORDER BY id DESC", (email_id,)).fetchall()
    return [_draft_row(r) for r in rows]


def list_drafts(status: str | None = "ready", limit: int = 100) -> list[dict]:
    q = """SELECT d.*, e.subject, e.sender_name, e.sender_email, e.received, a.priority
           FROM drafts d JOIN emails e ON e.id=d.email_id LEFT JOIN assessments a ON a.email_id=d.email_id"""
    args: list = []
    if status:
        q += " WHERE d.status=?"
        args.append(status)
    q += " ORDER BY d.id DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        d = _draft_row(r)
        d.update({"subject": r["subject"], "sender_name": r["sender_name"], "sender_email": r["sender_email"],
                  "received": r["received"], "priority": r["priority"]})
        out.append(d)
    return out


def _draft_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "email_id": r["email_id"], "body_text": r["body_text"], "attempts": r["attempts"],
        "checks": json.loads(r["checks"] or "{}"), "passed": bool(r["passed"]), "notes": r["notes"],
        "outlook_draft_id": r["outlook_draft_id"], "status": r["status"], "created_at": r["created_at"],
    }


def record_correction(email_id: str, to_priority: str) -> None:
    with _lock:
        row = conn().execute(
            "SELECT e.subject, e.sender_email, e.body_text, a.priority FROM emails e JOIN assessments a ON a.email_id=e.id WHERE e.id=?",
            (email_id,),
        ).fetchone()
        if not row:
            raise KeyError(email_id)
        conn().execute(
            "INSERT INTO corrections(email_id,subject,sender_email,snippet,from_priority,to_priority,created_at) VALUES(?,?,?,?,?,?,?)",
            (email_id, row["subject"], row["sender_email"], (row["body_text"] or "")[:300], row["priority"], to_priority, _now()),
        )
        conn().execute("UPDATE assessments SET user_priority=? WHERE email_id=?", (to_priority, email_id))
        conn().commit()


def set_handled(email_id: str, handled: bool) -> None:
    with _lock:
        conn().execute("UPDATE assessments SET handled_at=? WHERE email_id=?", (_now() if handled else None, email_id))
        conn().commit()


def handled_count_since(since_iso: str) -> int:
    with _lock:
        return conn().execute("SELECT COUNT(*) FROM assessments WHERE handled_at >= ?", (since_iso,)).fetchone()[0]


def set_user_tags(email_id: str, tags: list[str]) -> None:
    with _lock:
        conn().execute("UPDATE assessments SET user_tags=? WHERE email_id=?", (json.dumps(tags), email_id))
        conn().commit()


def recent_corrections(limit: int = 8) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT subject, sender_email, snippet, from_priority, to_priority FROM corrections ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_emails(priority: str | None = None, tag: str | None = None, review: bool | None = None,
                search: str | None = None, limit: int = 200, priorities: list[str] | None = None,
                since_iso: str | None = None, handled: bool | None = None) -> list[dict]:
    q = """SELECT e.*, a.priority, a.confidence, a.probabilities, a.signals, a.time_sensitivity, a.tags, a.tag_scores,
                  a.jev_priority, a.adjusted_by_rule, a.needs_review, a.user_priority, a.user_tags, a.thread_json, a.handled_at,
                  (SELECT COUNT(*) FROM drafts d WHERE d.email_id=e.id AND d.status='ready') AS ready_drafts
           FROM emails e JOIN assessments a ON a.email_id=e.id WHERE 1=1"""
    args: list = []
    if priority:
        q += " AND COALESCE(a.user_priority, a.priority)=?"
        args.append(priority)
    if priorities:
        q += f" AND COALESCE(a.user_priority, a.priority) IN ({','.join('?' * len(priorities))})"
        args += priorities
    if since_iso:
        q += " AND e.received >= ?"
        args.append(since_iso)
    if handled is True:
        q += " AND a.handled_at IS NOT NULL"
    elif handled is False:
        q += " AND a.handled_at IS NULL"
    if review:
        q += " AND a.needs_review=1"
    if search:
        q += " AND (e.subject LIKE ? OR e.sender_name LIKE ? OR e.sender_email LIKE ? OR e.body_text LIKE ?)"
        like = f"%{search}%"
        args += [like, like, like, like]
    q += " ORDER BY e.received DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        d = _email_row(r)
        if tag and tag not in d["tags"]:
            continue
        out.append(d)
    return out


def get_email(email_id: str) -> dict | None:
    with _lock:
        r = conn().execute(
            """SELECT e.*, a.priority, a.confidence, a.probabilities, a.signals, a.time_sensitivity, a.tags, a.tag_scores,
                      a.jev_priority, a.adjusted_by_rule, a.needs_review, a.user_priority, a.user_tags, a.thread_json, a.handled_at,
                      (SELECT COUNT(*) FROM drafts d WHERE d.email_id=e.id AND d.status='ready') AS ready_drafts
               FROM emails e LEFT JOIN assessments a ON a.email_id=e.id WHERE e.id=?""",
            (email_id,),
        ).fetchone()
    if not r:
        return None
    d = _email_row(r)
    d["drafts"] = drafts_for_email(email_id)
    return d


def _email_row(r: sqlite3.Row) -> dict:
    tags = json.loads(r["tags"] or "[]")
    user_tags = json.loads(r["user_tags"]) if r["user_tags"] else None
    return {
        "id": r["id"], "subject": r["subject"], "sender_name": r["sender_name"], "sender_email": r["sender_email"],
        "to": json.loads(r["to_json"] or "[]"), "cc": json.loads(r["cc_json"] or "[]"), "received": r["received"],
        "body_text": r["body_text"], "has_attachments": bool(r["has_attachments"]), "is_read": bool(r["is_read"]),
        "web_link": r["web_link"],
        "priority": r["user_priority"] or r["priority"], "model_priority": r["priority"], "user_priority": r["user_priority"],
        "confidence": r["confidence"], "probabilities": json.loads(r["probabilities"] or "{}"),
        "signals": json.loads(r["signals"] or "{}"), "time_sensitivity": r["time_sensitivity"],
        "tags": user_tags if user_tags is not None else tags, "model_tags": tags,
        "tag_scores": json.loads(r["tag_scores"] or "{}"), "jev_priority": r["jev_priority"],
        "adjusted_by_rule": r["adjusted_by_rule"], "needs_review": bool(r["needs_review"]),
        "ready_drafts": r["ready_drafts"], "conversation_id": r["conversation_id"],
        "thread": json.loads(r["thread_json"]) if r["thread_json"] else None,
        "handled_at": r["handled_at"],
    }


def emails_since(since_iso: str, priorities: list[str] | None = None, review_only: bool = False, limit: int = 50) -> list[dict]:
    q = """SELECT e.id, e.subject, e.sender_name, e.sender_email, e.received, a.tags, a.user_tags,
                  COALESCE(a.user_priority, a.priority) AS priority, a.confidence, a.needs_review
           FROM emails e JOIN assessments a ON a.email_id=e.id WHERE e.received >= ?"""
    args: list = [since_iso]
    if priorities:
        q += f" AND COALESCE(a.user_priority, a.priority) IN ({','.join('?' * len(priorities))})"
        args += priorities
    if review_only:
        q += " AND a.needs_review=1 AND COALESCE(a.user_priority, a.priority) NOT IN ('junk')"
    q += " ORDER BY e.received DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        tags = json.loads(r["user_tags"]) if r["user_tags"] else json.loads(r["tags"] or "[]")
        out.append({"id": r["id"], "subject": r["subject"], "sender_name": r["sender_name"], "sender_email": r["sender_email"],
                    "received": r["received"], "tags": tags, "priority": r["priority"], "confidence": r["confidence"]})
    return out


def counts_since(since_iso: str) -> dict:
    with _lock:
        rows = conn().execute(
            """SELECT COALESCE(a.user_priority, a.priority), COUNT(*) FROM emails e JOIN assessments a ON a.email_id=e.id
               WHERE e.received >= ? GROUP BY 1""",
            (since_iso,),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def stats() -> dict:
    with _lock:
        c = conn()
        total = c.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
        by_priority = {r[0]: r[1] for r in c.execute(
            "SELECT COALESCE(user_priority, priority), COUNT(*) FROM assessments GROUP BY 1").fetchall()}
        review = c.execute("SELECT COUNT(*) FROM assessments WHERE needs_review=1").fetchone()[0]
        drafts = c.execute("SELECT COUNT(*) FROM drafts WHERE status='ready'").fetchone()[0]
        tokens = c.execute("SELECT COALESCE(SUM(input_tokens),0) FROM assessments").fetchone()[0]
    return {"total": total, "by_priority": by_priority, "needs_review": review, "ready_drafts": drafts,
            "jev_input_tokens": tokens, "last_run": get_meta("last_run"), "last_run_note": get_meta("last_run_note")}
