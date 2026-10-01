"""質問を受け付けてエージェントに渡し、結果を記録する。CLI と Slack で共通に使う"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from .agent import AgentAnswer, AgentRunner
from .config import CLI_USER_ID, Settings
from .pricing import PriceTable
from .store import QuestionRecord, Store

log = logging.getLogger(__name__)


class NotAllowedError(Exception):
    pass


class DailyLimitError(Exception):
    def __init__(self, limit: int):
        super().__init__(limit)
        self.limit = limit


@dataclass
class ServiceAnswer:
    answer: AgentAnswer
    virtual_cost_usd: float | None
    question_id: int
    resumed: bool


class QAService:
    def __init__(
        self,
        settings: Settings,
        runner: AgentRunner,
        store: Store,
        prices: PriceTable,
        session_deleter: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.runner = runner
        self.store = store
        self.prices = prices
        self.session_deleter = session_deleter
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        # 同じスレッドで続けて質問されたとき、同じセッションを同時に再開しないようにする
        self._thread_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def is_allowed(self, user_id: str) -> bool:
        if user_id == CLI_USER_ID:
            return True
        return self.settings.is_slack_user_allowed(user_id)

    def check_quota(self, user_id: str) -> None:
        limit = self.settings.daily_limit_per_user
        if user_id != CLI_USER_ID and limit > 0 and self.store.count_today(user_id, self.settings.timezone) >= limit:
            raise DailyLimitError(limit)

    async def ask(
        self,
        user_id: str,
        question: str,
        thread_key: str | None = None,
        channel: str | None = None,
    ) -> ServiceAnswer:
        if not self.is_allowed(user_id):
            raise NotAllowedError(user_id)
        self.check_quota(user_id)

        lock = self._thread_locks[thread_key] if thread_key else asyncio.Lock()
        async with lock, self._semaphore:
            resume = self.store.get_session(thread_key) if thread_key else None
            try:
                answer = await self.runner.ask(question, resume_session_id=resume)
            except Exception:
                if resume is None:
                    raise
                # セッションの記録が消えているなどで再開できなければ、新しい会話として答える
                log.warning("セッション %s を再開できなかったため、新しい会話として答えます", resume)
                resume = None
                answer = await self.runner.ask(question, resume_session_id=None)
            if thread_key and answer.session_id and not answer.is_error:
                self.store.set_session(thread_key, answer.session_id)

        priced = {m: (u, self.prices.cost(m, u)) for m, u in answer.model_usage.items()}
        costs = [c for _, c in priced.values()]
        virtual = None if (not costs or any(c is None for c in costs)) else sum(costs)  # type: ignore[arg-type]
        keep_text = self.settings.log_question_text

        qid = self.store.add_question(
            QuestionRecord(
                user_id=user_id,
                channel=channel,
                thread_key=thread_key,
                question=question if keep_text else None,
                answer=answer.text if keep_text else None,
                answer_chars=len(answer.text),
                is_error=answer.is_error,
                subtype=answer.subtype,
                num_turns=answer.num_turns,
                duration_ms=answer.duration_ms,
                auth_mode=self.settings.auth_mode,
                auth_source=answer.auth_source,
                configured_model=self.settings.model,
                session_id=answer.session_id,
                resumed=resume is not None,
                permission_denials=answer.permission_denials,
                sdk_cost_usd=answer.sdk_cost_usd,
                virtual_cost_usd=virtual,
                model_usage=priced,
                cli_version=answer.cli_version,
            )
        )
        return ServiceAnswer(answer=answer, virtual_cost_usd=virtual, question_id=qid, resumed=resume is not None)

    def rate(self, question_id: int, user_id: str, rating: int, reason: str | None = None) -> None:
        self.store.set_feedback(question_id, user_id, 1 if rating > 0 else -1, reason)

    def purge(self) -> int:
        """保存期間を過ぎた記録と会話セッションを消し、消したセッションの数を返す"""
        sessions = self.store.purge(self.settings.retention_days, self.settings.session_retention_days)
        if self.session_deleter:
            for sid in sessions:
                try:
                    self.session_deleter(sid)
                except Exception:
                    log.exception("セッション %s を消せませんでした", sid)
        return len(sessions)
