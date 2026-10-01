"""質問を受け付けてエージェントに渡し、結果を記録する。CLI と Slack で共通に使う"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from .agent import AgentAnswer, AgentRunner
from .config import CLI_USER_ID, Settings
from .pricing import PriceTable
from .store import QuestionRecord, Store


class NotAllowedError(Exception):
    pass


@dataclass
class ServiceAnswer:
    answer: AgentAnswer
    virtual_cost_usd: float | None
    question_id: int
    resumed: bool


class QAService:
    def __init__(self, settings: Settings, runner: AgentRunner, store: Store, prices: PriceTable):
        self.settings = settings
        self.runner = runner
        self.store = store
        self.prices = prices
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        # 同じスレッドで続けて質問されたとき、同じセッションを同時に再開しないようにする
        self._thread_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def is_allowed(self, user_id: str) -> bool:
        if user_id == CLI_USER_ID:
            return True
        return self.settings.is_slack_user_allowed(user_id)

    async def ask(
        self,
        user_id: str,
        question: str,
        thread_key: str | None = None,
        channel: str | None = None,
    ) -> ServiceAnswer:
        if not self.is_allowed(user_id):
            raise NotAllowedError(user_id)

        lock = self._thread_locks[thread_key] if thread_key else asyncio.Lock()
        async with lock, self._semaphore:
            resume = self.store.get_session(thread_key) if thread_key else None
            try:
                answer = await self.runner.ask(question, resume_session_id=resume)
            except Exception:
                if resume is None:
                    raise
                # セッションのファイルが消えているなどで再開できなければ、新しい会話として答える
                resume = None
                answer = await self.runner.ask(question, resume_session_id=None)
            if thread_key and answer.session_id and not answer.is_error:
                self.store.set_session(thread_key, answer.session_id)

        priced = {m: (u, self.prices.cost(m, u)) for m, u in answer.model_usage.items()}
        costs = [c for _, c in priced.values()]
        virtual = None if (not costs or any(c is None for c in costs)) else sum(costs)  # type: ignore[arg-type]

        qid = self.store.add_question(
            QuestionRecord(
                user_id=user_id,
                channel=channel,
                thread_key=thread_key,
                question=question if self.settings.log_question_text else None,
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
            )
        )
        return ServiceAnswer(answer=answer, virtual_cost_usd=virtual, question_id=qid, resumed=resume is not None)
