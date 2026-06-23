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
    answered_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, package_id, question_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
"""


def init_db() -> None:
    with _conn() as cx:
        cx.executescript(_SCHEMA)


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
                   (SELECT COUNT(*) FROM answers a WHERE a.session_id = s.id AND a.correct = 1) AS correct
            FROM sessions s WHERE s.student_email = ?
            ORDER BY s.updated_at DESC
            """,
            (email,),
        ).fetchall()
    return [dict(r) for r in rows]


def create_session(email: str, name: str) -> dict:
    sid = _gen_id()
    now = _now()
    with _conn() as cx:
        cx.execute(
            "INSERT INTO sessions(id, student_email, name, created_at, updated_at) VALUES(?,?,?,?,?)",
            (sid, email, name, now, now),
        )
    return {"id": sid, "name": name, "created_at": now, "updated_at": now,
            "package_count": 0, "answered": 0, "correct": 0}


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


# ---- answers -----------------------------------------------------------------

def save_answer(email: str, sid: str, package_id: str, question_id: str,
                selected_ids: list, correct: bool | None) -> bool:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return False
        cx.execute(
            "INSERT OR IGNORE INTO session_packages(session_id, package_id, added_at) VALUES(?,?,?)",
            (sid, package_id, _now()),
        )
        cx.execute(
            """
            INSERT INTO answers(session_id, package_id, question_id, selected_ids, correct, answered_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(session_id, package_id, question_id) DO UPDATE SET
                selected_ids=excluded.selected_ids, correct=excluded.correct, answered_at=excluded.answered_at
            """,
            (sid, package_id, question_id, json.dumps(list(selected_ids or [])),
             None if correct is None else int(bool(correct)), _now()),
        )
        _touch(cx, sid)
    return True


def get_answers(email: str, sid: str, package_id: str | None = None) -> list[dict]:
    with _conn() as cx:
        if not _owned(cx, email, sid):
            return []
        if package_id:
            rows = cx.execute(
                "SELECT package_id, question_id, selected_ids, correct, answered_at "
                "FROM answers WHERE session_id=? AND package_id=?", (sid, package_id)).fetchall()
        else:
            rows = cx.execute(
                "SELECT package_id, question_id, selected_ids, correct, answered_at "
                "FROM answers WHERE session_id=?", (sid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["selected_ids"] = json.loads(d["selected_ids"])
        d["correct"] = None if d["correct"] is None else bool(d["correct"])
        out.append(d)
    return out


def _progress(cx, sid: str) -> dict:
    total = cx.execute("SELECT COUNT(*) c FROM answers WHERE session_id=?", (sid,)).fetchone()["c"]
    correct = cx.execute("SELECT COUNT(*) c FROM answers WHERE session_id=? AND correct=1", (sid,)).fetchone()["c"]
    by_pkg = [dict(r) for r in cx.execute(
        "SELECT package_id, COUNT(*) answered, SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct "
        "FROM answers WHERE session_id=? GROUP BY package_id", (sid,))]
    return {"answered": total, "correct": correct, "by_package": by_pkg}


# Initialise the schema on import (cheap, idempotent).
init_db()
