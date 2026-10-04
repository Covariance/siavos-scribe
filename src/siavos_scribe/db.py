"""SQLite storage: users, asked questions, saved messages."""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY,
    chat_id          INTEGER NOT NULL,
    username         TEXT,
    interval_seconds INTEGER NOT NULL,
    next_due_at      INTEGER NOT NULL,
    paused           INTEGER NOT NULL DEFAULT 0,
    cycle            INTEGER NOT NULL DEFAULT 0,
    last_question_id INTEGER,
    created_at       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS questions_asked (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    item_id       TEXT NOT NULL,
    item_text     TEXT NOT NULL,
    section       TEXT NOT NULL,
    subsection    TEXT NOT NULL,
    cycle         INTEGER NOT NULL,
    trigger       TEXT NOT NULL,
    tg_message_id INTEGER,
    asked_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asked_user_cycle ON questions_asked(user_id, cycle);
CREATE TABLE IF NOT EXISTS messages (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL,
    tg_message_id          INTEGER NOT NULL,
    kind                   TEXT NOT NULL,
    text                   TEXT,
    file_path              TEXT,
    duration               INTEGER,
    question_id            INTEGER,
    reply_to_tg_message_id INTEGER,
    created_at             INTEGER NOT NULL,
    raw_json               TEXT
);
CREATE INDEX IF NOT EXISTS idx_asked_user_tg_message ON questions_asked(user_id, tg_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_question ON messages(question_id);
"""


@dataclass(frozen=True)
class User:
    user_id: int
    chat_id: int
    username: str | None
    interval_seconds: int
    next_due_at: int
    paused: bool
    cycle: int
    last_question_id: int | None


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        row["user_id"], row["chat_id"], row["username"], row["interval_seconds"],
        row["next_due_at"], bool(row["paused"]), row["cycle"], row["last_question_id"],
    )


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- users ---
    def get_user(self, user_id: int) -> User | None:
        return _user(self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())

    def upsert_user(self, user_id: int, chat_id: int, username: str | None, default_interval: int) -> User:
        now = int(time.time())
        with self.conn:
            self.conn.execute(
                "INSERT INTO users(user_id, chat_id, username, interval_seconds, next_due_at, created_at)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, username=excluded.username",
                (user_id, chat_id, username, default_interval, now + default_interval, now),
            )
        return self.get_user(user_id)  # type: ignore[return-value]

    def due_users(self, now: int) -> list[User]:
        rows = self.conn.execute(
            "SELECT * FROM users WHERE paused=0 AND next_due_at<=?", (now,)
        ).fetchall()
        return [_user(r) for r in rows]  # type: ignore[misc]

    def set_interval(self, user_id: int, seconds: int, now: int | None = None) -> None:
        """Choosing a cadence also (re)enables scheduled questions."""
        now = int(time.time()) if now is None else now
        with self.conn:
            self.conn.execute(
                "UPDATE users SET interval_seconds=?, next_due_at=?, paused=0 WHERE user_id=?",
                (seconds, now + seconds, user_id),
            )

    def set_paused(self, user_id: int, paused: bool, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        with self.conn:
            if not paused:  # resuming restarts the countdown (a no-op /resume leaves it alone)
                self.conn.execute(
                    "UPDATE users SET next_due_at=? + interval_seconds WHERE user_id=? AND paused=1",
                    (now, user_id),
                )
            self.conn.execute("UPDATE users SET paused=? WHERE user_id=?", (int(paused), user_id))

    def restart_timer(self, user_id: int, now: int | None = None) -> None:
        """Next question one interval from now, using the interval stored right now (not a stale copy)."""
        now = int(time.time()) if now is None else now
        with self.conn:
            self.conn.execute(
                "UPDATE users SET next_due_at=? + interval_seconds WHERE user_id=?", (now, user_id)
            )

    # --- questions ---
    def asked_item_ids(self, user_id: int, cycle: int) -> set[str]:
        rows = self.conn.execute(
            "SELECT item_id FROM questions_asked WHERE user_id=? AND cycle=?", (user_id, cycle)
        ).fetchall()
        return {r["item_id"] for r in rows}

    def record_question(
        self, user_id: int, item_id: str, item_text: str, section: str, subsection: str,
        cycle: int, trigger: str, tg_message_id: int | None, now: int | None = None,
    ) -> int:
        now = int(time.time()) if now is None else now
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO questions_asked(user_id, item_id, item_text, section, subsection, cycle,"
                " trigger, tg_message_id, asked_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (user_id, item_id, item_text, section, subsection, cycle, trigger, tg_message_id, now),
            )
            qid = cur.lastrowid
            # The cycle only advances once a question of the new cycle was actually sent.
            self.conn.execute(
                "UPDATE users SET last_question_id=?, cycle=MAX(cycle, ?) WHERE user_id=?", (qid, cycle, user_id)
            )
        return qid  # type: ignore[return-value]

    def last_proofread(self, user_id: int) -> dict[str, int]:
        """item_id -> id of the newest proof-read row for it (higher = more recent)."""
        rows = self.conn.execute(
            "SELECT item_id, MAX(id) AS last FROM questions_asked"
            " WHERE user_id=? AND trigger='proofread' GROUP BY item_id",
            (user_id,),
        ).fetchall()
        return {r["item_id"]: r["last"] for r in rows}

    def question_by_tg_message(self, user_id: int, tg_message_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM questions_asked WHERE user_id=? AND tg_message_id=?", (user_id, tg_message_id)
        ).fetchone()
        return row["id"] if row else None

    # --- messages ---
    def save_message(
        self, user_id: int, tg_message_id: int, kind: str, text: str | None, file_path: str | None,
        duration: int | None, question_id: int | None, reply_to_tg_message_id: int | None,
        raw_json: str | None, now: int | None = None,
    ) -> int:
        now = int(time.time()) if now is None else now
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO messages(user_id, tg_message_id, kind, text, file_path, duration, question_id,"
                " reply_to_tg_message_id, created_at, raw_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (user_id, tg_message_id, kind, text, file_path, duration, question_id,
                 reply_to_tg_message_id, now, raw_json),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def stats(self, user_id: int) -> tuple[int, int]:
        """(questions asked, messages saved)."""
        asked = self.conn.execute(
            "SELECT COUNT(*) FROM questions_asked WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        saved = self.conn.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (user_id,)).fetchone()[0]
        return asked, saved

    # --- admin queries ---
    def list_users(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT u.*,"
            " (SELECT COUNT(*) FROM questions_asked q WHERE q.user_id=u.user_id) AS asked,"
            " (SELECT COUNT(*) FROM messages m WHERE m.user_id=u.user_id) AS saved,"
            " (SELECT MAX(created_at) FROM messages m WHERE m.user_id=u.user_id) AS last_message_at"
            " FROM users u ORDER BY u.created_at"
        ).fetchall()

    def find_user(self, ref: str) -> User | None:
        """Look a user up by numeric ID or @username (case-insensitive)."""
        ref = ref.strip()
        if ref.lstrip("-").isdecimal():  # isdigit() accepts "²", which int() rejects
            return self.get_user(int(ref))
        row = self.conn.execute(
            "SELECT * FROM users WHERE LOWER(username)=?", (ref.lstrip("@").lower(),)
        ).fetchone()
        return _user(row)

    def recent_questions(self, user_id: int | None, limit: int) -> list[sqlite3.Row]:
        """Newest first; `answers` counts messages attributed to each question."""
        where, args = ("WHERE q.user_id=?", (user_id,)) if user_id is not None else ("", ())
        return self.conn.execute(
            "SELECT q.*, (SELECT COUNT(*) FROM messages m WHERE m.question_id=q.id) AS answers"
            f" FROM questions_asked q {where} ORDER BY q.id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()

    def recent_messages(self, user_id: int | None, limit: int) -> list[sqlite3.Row]:
        """Newest first, each joined with the question it is attributed to."""
        where, args = ("WHERE m.user_id=?", (user_id,)) if user_id is not None else ("", ())
        return self.conn.execute(
            "SELECT m.*, q.item_text AS question_text, q.section AS question_section,"
            " q.trigger AS question_trigger"
            " FROM messages m LEFT JOIN questions_asked q ON q.id=m.question_id"
            f" {where} ORDER BY m.id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()

    def message_by_id(self, message_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()

    def export(self, user_id: int | None) -> dict:
        """Everything stored, oldest first, as plain dicts (raw_json parsed out for readability)."""
        def rows(table: str, order: str) -> list[dict]:
            if user_id is None:
                cur = self.conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
            else:
                cur = self.conn.execute(f"SELECT * FROM {table} WHERE user_id=? ORDER BY {order}", (user_id,))
            return [dict(r) for r in cur.fetchall()]

        users = [dict(r) for r in self.list_users()]
        if user_id is not None:
            users = [u for u in users if u["user_id"] == user_id]
        return {
            "exported_at": int(time.time()),
            "users": users,
            "questions_asked": rows("questions_asked", "id"),
            "messages": rows("messages", "id"),
        }
