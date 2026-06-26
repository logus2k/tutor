"""Study Sessions — per-student persistence backed by SQLite.

A "study session" is a named workspace owned by a Google-authenticated student
(identity comes from the oauth2-proxy `X-Forwarded-Email` header — see service.py).
A session scopes a set of packages and keeps the student's per-question answers
and results, so progress survives reloads and container restarts.

Login is OPTIONAL: anonymous users still use the app (answers stay ephemeral in
the browser); sessions/persistence only exist once signed in.

Storage is a single SQLite file in the read-write data mount (`data/tutor.db`),
so it persists across image rebuilds. stdlib sqlite3, WAL mode, a fresh
short-lived connection per call (simple + safe for this scale).
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import datetime
from contextlib import contextmanager

from etl import catalog

DB_PATH = os.getenv("TUTOR_DB", os.path.join(catalog.ROOT, "data", "tutor.db"))


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    cx = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
    cx.row_factory = sqlite3.Row
    try:
        cx.execute("PRAGMA journal_mode=WAL")
        cx.execute("PRAGMA foreign_keys=ON")
        cx.execute("PRAGMA busy_timeout=5000")
        yield cx
        cx.commit()
    finally:
        cx.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    email      TEXT PRIMARY KEY,
    name       TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    student_email TEXT NOT NULL,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (student_email) REFERENCES students(email)
);
CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_email);
CREATE TABLE IF NOT EXISTS session_packages (
    session_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, package_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS answers (
    session_id   TEXT NOT NULL,
    package_id   TEXT NOT NULL,
    question_id  TEXT NOT NULL,
    selected_ids TEXT NOT NULL,   -- JSON array of option ids
    correct      INTEGER,         -- 1 / 0 / NULL
    attempts     INTEGER NOT NULL DEFAULT 1,  -- submissions for this question (retries)
    answered_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, package_id, question_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS mastery (
    student_email TEXT NOT NULL,
    concept_id    TEXT NOT NULL,
    title         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    correct       INTEGER NOT NULL DEFAULT 0,
    ability       REAL NOT NULL DEFAULT 0,   -- 0..1, ELO/IRT-lite estimate
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (student_email, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_mastery_student ON mastery(student_email);
CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_email TEXT NOT NULL,
    kind          TEXT NOT NULL,         -- review | reminder | job | info
    title         TEXT NOT NULL,
    body          TEXT,
    dedup_key     TEXT,                  -- collapse repeats (per student)
    created_at    TEXT NOT NULL,
    read_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_dedup
    ON notifications(student_email, dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notif_student ON notifications(student_email, created_at);
"""


def init_db() -> None:
    with _conn() as cx:
        cx.executescript(_SCHEMA)
        # Migrate older DBs that predate retry tracking.
        cols = [r["name"] for r in cx.execute("PRAGMA table_info(answers)")]
        if "attempts" not in cols:
            cx.execute("ALTER TABLE answers ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")


# ---- students ----------------------------------------------------------------

def upsert_student(email: str, name: str | None = None) -> None:
    with _conn() as cx:
        cx.execute(
            "INSERT INTO students(email, name, created_at) VALUES(?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET name=COALESCE(excluded.name, students.name)",
            (email, name, _now()),
        )


# ---- sessions ----------------------------------------------------------------

def _gen_id() -> str:
    return "ss-" + secrets.token_hex(8)


def list_sessions(email: str) -> list[dict]:
    with _conn() as cx:
        rows = cx.execute(
            """
            SELECT s.id, s.name, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM session_packages p WHERE p.session_id = s.id) AS package_count,
                   (SELECT COUNT(*) FROM answers a WHERE a.session_id = s.id) AS answered,
                   (SELECT COUNT(*) FROM answers a WHERE a.session_id = s.id AND a.correct = 1) AS correct,
                   (SELECT COALESCE(SUM(CASE WHEN a.correct = 1 THEN 1.0 / a.attempts ELSE 0 END), 0)
                      FROM answers a WHERE a.session_id = s.id) AS score
            FROM sessions s WHERE s.student_email = ?
            ORDER BY s.updated_at DESC
            """,
            (email,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["score"] = round(d.get("score") or 0, 2)
        out.append(d)
    return out


def create_session(email: str, name: str) -> dict:
    sid = _gen_id()
    now = _now()
    with _conn() as cx:
        cx.execute(
            "INSERT INTO sessions(id, student_email, name, created_at, updated_at) VALUES(?,?,?,?,?)",
            (sid, email, name, now, now),
        )
    return {"id": sid, "name": name, "created_at": now, "updated_at": now,
            "package_count": 0, "answered": 0, "correct": 0, "score": 0}


def _owned(cx, email: str, sid: str) -> bool:
    r = cx.execute("SELECT 1 FROM sessions WHERE id=? AND student_email=?", (sid, email)).fetchone()
    return r is not None


def get_session(email: str, sid: str) -> dict | None:
    with _conn() as cx:
        s = cx.execute(
            "SELECT id, name, created_at, updated_at FROM sessions WHERE id=? AND student_email=?",
            (sid, email),
        ).fetchone()
        if not s:
            return None
        pkgs = [r["package_id"] for r in cx.execute(
            "SELECT package_id FROM session_packages WHERE session_id=? ORDER BY added_at", (sid,))]
        prog = _progress(cx, sid)
    out = dict(s)
    out["packages"] = pkgs
    out["progress"] = prog
    return out


def rename_session(email: str, sid: str, name: str) -> bool:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return False
        cx.execute("UPDATE sessions SET name=?, updated_at=? WHERE id=?", (name, _now(), sid))
    return True


def delete_session(email: str, sid: str) -> bool:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return False
        cx.execute("DELETE FROM sessions WHERE id=?", (sid,))
    return True


def _touch(cx, sid: str) -> None:
    cx.execute("UPDATE sessions SET updated_at=? WHERE id=?", (_now(), sid))


# ---- packages in a session ---------------------------------------------------

def add_package(email: str, sid: str, package_id: str) -> bool:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return False
        cx.execute(
            "INSERT OR IGNORE INTO session_packages(session_id, package_id, added_at) VALUES(?,?,?)",
            (sid, package_id, _now()),
        )
        _touch(cx, sid)
    return True


def remove_package(email: str, sid: str, package_id: str) -> bool:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return False
        cx.execute("DELETE FROM session_packages WHERE session_id=? AND package_id=?",
                   (sid, package_id))
        _touch(cx, sid)
    return True


def sessions_with_package(email: str, package_id: str) -> list[str]:
    """Session ids owned by `email` that currently contain `package_id`."""
    with _conn() as cx:
        return [r["session_id"] for r in cx.execute(
            "SELECT sp.session_id FROM session_packages sp "
            "JOIN sessions s ON s.id = sp.session_id "
            "WHERE s.student_email=? AND sp.package_id=?", (email, package_id))]


# ---- answers -----------------------------------------------------------------

def save_answer(email: str, sid: str, package_id: str, question_id: str,
                selected_ids: list, correct: bool | None, attempts: int = 1) -> bool:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return False
        cx.execute(
            "INSERT OR IGNORE INTO session_packages(session_id, package_id, added_at) VALUES(?,?,?)",
            (sid, package_id, _now()),
        )
        cx.execute(
            """
            INSERT INTO answers(session_id, package_id, question_id, selected_ids, correct, attempts, answered_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(session_id, package_id, question_id) DO UPDATE SET
                selected_ids=excluded.selected_ids, correct=excluded.correct,
                attempts=excluded.attempts, answered_at=excluded.answered_at
            """,
            (sid, package_id, question_id, json.dumps(list(selected_ids or [])),
             None if correct is None else int(bool(correct)), max(1, int(attempts or 1)), _now()),
        )
        _touch(cx, sid)
    return True


def get_answers(email: str, sid: str, package_id: str | None = None) -> list[dict]:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return []
        if package_id:
            rows = cx.execute(
                "SELECT package_id, question_id, selected_ids, correct, attempts, answered_at "
                "FROM answers WHERE session_id=? AND package_id=?", (sid, package_id)).fetchall()
        else:
            rows = cx.execute(
                "SELECT package_id, question_id, selected_ids, correct, attempts, answered_at "
                "FROM answers WHERE session_id=?", (sid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["selected_ids"] = json.loads(d["selected_ids"])
        d["correct"] = None if d["correct"] is None else bool(d["correct"])
        out.append(d)
    return out


def _progress(cx, sid: str) -> dict:
    row = cx.execute(
        "SELECT COUNT(*) answered, "
        "SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct, "
        "COALESCE(SUM(attempts), 0) attempts, "
        "COALESCE(SUM(CASE WHEN correct=1 THEN 1.0/attempts ELSE 0 END), 0) score "
        "FROM answers WHERE session_id=?", (sid,)).fetchone()
    by_pkg = [dict(r) for r in cx.execute(
        "SELECT package_id, COUNT(*) answered, "
        "SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct, "
        "COALESCE(SUM(attempts), 0) attempts, "
        "ROUND(COALESCE(SUM(CASE WHEN correct=1 THEN 1.0/attempts ELSE 0 END), 0), 2) score "
        "FROM answers WHERE session_id=? GROUP BY package_id", (sid,))]
    return {"answered": row["answered"] or 0, "correct": row["correct"] or 0,
            "attempts": row["attempts"] or 0, "score": round(row["score"] or 0, 2),
            "by_package": by_pkg}


# ---- leaderboard / Wall of Fame ----------------------------------------------

def _mask_email(email: str) -> str:
    """A privacy-preserving display handle for other students on the public board."""
    local, _, domain = (email or "").partition("@")
    if not local:
        return "anonymous"
    shown = local[:2] if len(local) > 2 else local
    return f"{shown}…@{domain}" if domain else f"{shown}…"


def leaderboard(package_id: str, email: str | None = None, mine: bool = False,
                limit: int = 50) -> list[dict]:
    """Best sessions for a package, ranked by score (retry-aware).

    Each row is one *session's* performance on the package — the same student
    can appear multiple times (one row per session they studied it in). When
    `mine` is set, only the requesting student's sessions are returned.
    `email`, when provided, marks the requester's own rows (`is_me`) and lets
    them see their real handle; other students are masked.
    """
    sql = """
        SELECT a.session_id, s.name AS session_name, s.student_email,
               st.name AS student_name,
               COUNT(*) AS answered,
               SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END) AS correct,
               SUM(a.attempts) AS attempts,
               SUM(CASE WHEN a.correct=1 THEN 1.0/a.attempts ELSE 0 END) AS score,
               MAX(a.answered_at) AS last_at
        FROM answers a
        JOIN sessions s ON s.id = a.session_id
        LEFT JOIN students st ON st.email = s.student_email
        WHERE a.package_id = ?
    """
    params: list = [package_id]
    if mine:
        sql += " AND s.student_email = ? "
        params.append((email or "").lower())
    sql += " GROUP BY a.session_id ORDER BY score DESC, attempts ASC, last_at ASC LIMIT ? "
    params.append(int(limit))

    me = (email or "").lower()
    with _conn() as cx:
        rows = cx.execute(sql, params).fetchall()
    out = []
    for rank, r in enumerate(rows, start=1):
        d = dict(r)
        em = (d.pop("student_email", "") or "").lower()
        name = d.pop("student_name", None)
        is_me = bool(me) and em == me
        d["rank"] = rank
        d["is_me"] = is_me
        d["who"] = "You" if is_me else (name or _mask_email(em))
        d["score"] = round(d.get("score") or 0, 2)
        out.append(d)
    return out


# ---- mastery (per-concept ability) ------------------------------------------

def update_mastery(email: str, concept_id: str, title: str | None,
                   correct: bool, attempts: int = 1) -> None:
    """Update a student's ability on a concept from one graded outcome.

    ELO/IRT-lite: ability += k·(target − ability), target = 1 if correct else 0.
    First-try evidence weighs more (larger k); later attempts move it less.
    """
    if not email or not concept_id:
        return
    k = 0.40 if (attempts or 1) <= 1 else 0.22
    target = 1.0 if correct else 0.0
    with _conn() as cx:
        row = cx.execute(
            "SELECT attempts, correct, ability FROM mastery WHERE student_email=? AND concept_id=?",
            (email, concept_id)).fetchone()
        if row:
            ability = max(0.0, min(1.0, row["ability"] + k * (target - row["ability"])))
            cx.execute(
                "UPDATE mastery SET title=COALESCE(?, title), attempts=attempts+1, "
                "correct=correct+?, ability=?, updated_at=? WHERE student_email=? AND concept_id=?",
                (title, 1 if correct else 0, ability, _now(), email, concept_id))
        else:
            ability = max(0.0, min(1.0, k * target))
            cx.execute(
                "INSERT INTO mastery(student_email, concept_id, title, attempts, correct, ability, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (email, concept_id, title, 1, 1 if correct else 0, ability, _now()))


def get_mastery(email: str, concept_ids: set | None = None) -> list[dict]:
    with _conn() as cx:
        rows = cx.execute(
            "SELECT concept_id, title, attempts, correct, ability, updated_at "
            "FROM mastery WHERE student_email=? ORDER BY ability ASC", (email,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if concept_ids is not None and d["concept_id"] not in concept_ids:
            continue
        d["ability"] = round(d["ability"], 3)
        d["mastered"] = d["ability"] >= 0.8 and d["attempts"] >= 2
        out.append(d)
    return out


# ---- notifications ----------------------------------------------------------

def add_notification(email: str, kind: str, title: str,
                     body: str | None = None, dedup_key: str | None = None) -> None:
    if not email:
        return
    with _conn() as cx:
        cx.execute(
            "INSERT OR IGNORE INTO notifications(student_email, kind, title, body, dedup_key, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, kind, title, body, dedup_key, _now()))


def list_notifications(email: str, limit: int = 50) -> list[dict]:
    with _conn() as cx:
        rows = cx.execute(
            "SELECT id, kind, title, body, created_at, read_at FROM notifications "
            "WHERE student_email=? ORDER BY created_at DESC LIMIT ?", (email, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["read"] = d.pop("read_at") is not None
        out.append(d)
    return out


def unread_count(email: str) -> int:
    with _conn() as cx:
        return cx.execute(
            "SELECT COUNT(*) c FROM notifications WHERE student_email=? AND read_at IS NULL",
            (email,)).fetchone()["c"]


def mark_read(email: str, notif_id: int | None = None) -> None:
    with _conn() as cx:
        if notif_id is None:
            cx.execute("UPDATE notifications SET read_at=? WHERE student_email=? AND read_at IS NULL",
                       (_now(), email))
        else:
            cx.execute("UPDATE notifications SET read_at=? WHERE student_email=? AND id=?",
                       (_now(), email, int(notif_id)))


# Initialise the schema on import (cheap, idempotent).
init_db()
