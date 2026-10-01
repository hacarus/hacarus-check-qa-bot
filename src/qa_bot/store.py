"""質問・回答・評価・トークン数と、スレッドと会話セッションの対応を SQLite に記録する"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
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
    answer TEXT,
    answer_chars INTEGER NOT NULL,
    is_error INTEGER NOT NULL,
    subtype TEXT,
    num_turns INTEGER,
    duration_ms INTEGER,
    auth_mode TEXT NOT NULL,
    auth_source TEXT,
    cli_version TEXT,
    configured_model TEXT NOT NULL,
    session_id TEXT,
    resumed INTEGER NOT NULL,
    permission_denials INTEGER NOT NULL DEFAULT 0,
    sdk_cost_usd REAL,
    virtual_cost_usd REAL
);
CREATE TABLE IF NOT EXISTS question_models (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    virtual_cost_usd REAL
);
CREATE TABLE IF NOT EXISTS feedback (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL,  -- 1 = 👍, -1 = 👎
    reason TEXT,
    rated_at TEXT NOT NULL,
    PRIMARY KEY (question_id, user_id)
);
CREATE TABLE IF NOT EXISTS threads (
    thread_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_asked_at ON questions(asked_at);
CREATE INDEX IF NOT EXISTS idx_questions_user ON questions(user_id, asked_at);
"""


@dataclass
class QuestionRecord:
    user_id: str
    channel: str | None
    thread_key: str | None
    question: str | None
    answer: str | None
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
    cli_version: str | None = None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        # 列を足す前に作った記録ファイルにも列を足す
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(questions)")}
        if "cli_version" not in cols:
            self.conn.execute("ALTER TABLE questions ADD COLUMN cli_version TEXT")

    def close(self) -> None:
        self.conn.close()

    # ----- スレッドとセッション -----

    def get_session(self, thread_key: str) -> str | None:
        row = self.conn.execute("SELECT session_id FROM threads WHERE thread_key = ?", (thread_key,)).fetchone()
        return row["session_id"] if row else None

    def set_session(self, thread_key: str, session_id: str) -> None:
        self.conn.execute(
            "INSERT INTO threads (thread_key, session_id, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(thread_key) DO UPDATE SET session_id = excluded.session_id, updated_at = excluded.updated_at",
            (thread_key, session_id, _iso(_now())),
        )
        self.conn.commit()

    # ----- 質問 -----

    def add_question(self, r: QuestionRecord) -> int:
        cur = self.conn.execute(
            "INSERT INTO questions (asked_at, user_id, channel, thread_key, question, answer, answer_chars, is_error,"
            " subtype, num_turns, duration_ms, auth_mode, auth_source, cli_version, configured_model, session_id,"
            " resumed, permission_denials, sdk_cost_usd, virtual_cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _iso(r.asked_at or _now()), r.user_id, r.channel, r.thread_key, r.question, r.answer, r.answer_chars,
                int(r.is_error), r.subtype, r.num_turns, r.duration_ms, r.auth_mode, r.auth_source, r.cli_version,
                r.configured_model, r.session_id, int(r.resumed), r.permission_denials, r.sdk_cost_usd,
                r.virtual_cost_usd,
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

    def count_today(self, user_id: str, tz: tzinfo, now: datetime | None = None) -> int:
        """その利用者が、指定したタイムゾーンでの今日に質問した件数"""
        local = (now or _now()).astimezone(tz)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE user_id = ? AND asked_at >= ? AND asked_at < ?",
            (user_id, _iso(start), _iso(start + timedelta(days=1))),
        ).fetchone()
        return int(row["n"])

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

    # ----- 評価 -----

    def set_feedback(self, question_id: int, user_id: str, rating: int, reason: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO feedback (question_id, user_id, rating, reason, rated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(question_id, user_id) DO UPDATE SET rating = excluded.rating,"
            " reason = COALESCE(excluded.reason, feedback.reason), rated_at = excluded.rated_at",
            (question_id, user_id, rating, reason, _iso(_now())),
        )
        self.conn.commit()

    def set_feedback_reason(self, question_id: int, user_id: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE feedback SET reason = ? WHERE question_id = ? AND user_id = ?", (reason, question_id, user_id)
        )
        self.conn.commit()

    def feedback_rows(self, month: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT f.* FROM feedback f JOIN questions q ON q.id = f.question_id"
        if month:
            return self.conn.execute(sql + " WHERE substr(q.asked_at, 1, 7) = ?", (month,)).fetchall()
        return self.conn.execute(sql).fetchall()

    # ----- 保存期間 -----

    def purge(self, retention_days: int, session_retention_days: int, now: datetime | None = None) -> list[str]:
        """保存期間を過ぎた記録を消し、消すべき会話セッションの ID を返す"""
        now = now or _now()
        cutoff = _iso(now - timedelta(days=session_retention_days))
        old = {r[0] for r in self.conn.execute("SELECT session_id FROM threads WHERE updated_at < ?", (cutoff,))}
        old |= {r[0] for r in self.conn.execute(
            "SELECT DISTINCT session_id FROM questions WHERE asked_at < ? AND session_id IS NOT NULL", (cutoff,)
        )}
        # まだ続いている会話のセッションは残す
        old -= {r[0] for r in self.conn.execute("SELECT session_id FROM threads WHERE updated_at >= ?", (cutoff,))}
        self.conn.execute("DELETE FROM threads WHERE updated_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM questions WHERE asked_at < ?", (_iso(now - timedelta(days=retention_days)),))
        self.conn.commit()
        return sorted(old)
