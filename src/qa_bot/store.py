"""質問ごとのトークン数と仮想料金、スレッドとセッションの対応を SQLite に記録する"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .pricing import TokenUsage

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at TEXT NOT NULL,
    user_id TEXT NOT NULL,
    channel TEXT,
    thread_key TEXT,
    question TEXT,
    answer_chars INTEGER NOT NULL,
    is_error INTEGER NOT NULL,
    subtype TEXT,
    num_turns INTEGER,
    duration_ms INTEGER,
    auth_mode TEXT NOT NULL,
    auth_source TEXT,
    configured_model TEXT NOT NULL,
    session_id TEXT,
    resumed INTEGER NOT NULL,
    permission_denials INTEGER NOT NULL DEFAULT 0,
    sdk_cost_usd REAL,
    virtual_cost_usd REAL
);
CREATE TABLE IF NOT EXISTS question_models (
    question_id INTEGER NOT NULL REFERENCES questions(id),
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    virtual_cost_usd REAL
);
CREATE TABLE IF NOT EXISTS threads (
    thread_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_asked_at ON questions(asked_at);
"""


@dataclass
class QuestionRecord:
    user_id: str
    channel: str | None
    thread_key: str | None
    question: str | None
    answer_chars: int
    is_error: bool
    subtype: str
    num_turns: int
    duration_ms: int
    auth_mode: str
    auth_source: str | None
    configured_model: str
    session_id: str | None
    resumed: bool
    permission_denials: int
    sdk_cost_usd: float | None
    virtual_cost_usd: float | None
    model_usage: dict[str, tuple[TokenUsage, float | None]]
    asked_at: datetime | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def get_session(self, thread_key: str) -> str | None:
        row = self.conn.execute("SELECT session_id FROM threads WHERE thread_key = ?", (thread_key,)).fetchone()
        return row["session_id"] if row else None

    def set_session(self, thread_key: str, session_id: str) -> None:
        self.conn.execute(
            "INSERT INTO threads (thread_key, session_id, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(thread_key) DO UPDATE SET session_id = excluded.session_id, updated_at = excluded.updated_at",
            (thread_key, session_id, _now()),
        )
        self.conn.commit()

    def add_question(self, r: QuestionRecord) -> int:
        asked_at = (r.asked_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO questions (asked_at, user_id, channel, thread_key, question, answer_chars, is_error, subtype,"
            " num_turns, duration_ms, auth_mode, auth_source, configured_model, session_id, resumed,"
            " permission_denials, sdk_cost_usd, virtual_cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asked_at, r.user_id, r.channel, r.thread_key, r.question, r.answer_chars, int(r.is_error),
                r.subtype, r.num_turns, r.duration_ms, r.auth_mode, r.auth_source, r.configured_model,
                r.session_id, int(r.resumed), r.permission_denials, r.sdk_cost_usd, r.virtual_cost_usd,
            ),
        )
        qid = int(cur.lastrowid)
        for model, (u, cost) in r.model_usage.items():
            self.conn.execute(
                "INSERT INTO question_models VALUES (?, ?, ?, ?, ?, ?, ?)",
                (qid, model, u.input, u.output, u.cache_write, u.cache_read, cost),
            )
        self.conn.commit()
        return qid

    def questions(self, month: str | None = None) -> list[sqlite3.Row]:
        """month は YYYY-MM(UTC)。None なら全期間"""
        if month:
            return self.conn.execute(
                "SELECT * FROM questions WHERE substr(asked_at, 1, 7) = ? ORDER BY id", (month,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM questions ORDER BY id").fetchall()

    def model_rows(self, month: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT m.* FROM question_models m JOIN questions q ON q.id = m.question_id"
        if month:
            return self.conn.execute(sql + " WHERE substr(q.asked_at, 1, 7) = ?", (month,)).fetchall()
        return self.conn.execute(sql).fetchall()
