"""Slack(Socket Mode)の受け口。処理本体は SlackHandlers に分け、Slack なしでもテストできるようにする"""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import Settings
from .pricing import PriceTable
from .report import format_summary, summarize
from .service import NotAllowedError, QAService

log = logging.getLogger(__name__)

MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
# Slack の1メッセージに収まるよう分割する長さ
CHUNK = 3500

NOT_ALLOWED_MESSAGE = "このボットは試験運用中のため、利用できるメンバーを限定しています。"
THINKING_MESSAGE = ":mag: リポジトリを調べています…"


def strip_mention(text: str) -> str:
    return MENTION_RE.sub("", text or "").strip()


def split_text(text: str, size: int = CHUNK) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


class SlackHandlers:
    def __init__(self, settings: Settings, service: QAService, prices: PriceTable):
        self.settings = settings
        self.service = service
        self.prices = prices

    async def handle_question(self, event: dict[str, Any], client: Any) -> None:
        if event.get("bot_id") or event.get("subtype"):
            return
        user = event.get("user", "")
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        question = strip_mention(event.get("text", ""))

        if not self.service.is_allowed(user):
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=NOT_ALLOWED_MESSAGE)
            return
        if not question:
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="質問を書いてください。")
            return

        placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=THINKING_MESSAGE)
        try:
            result = await self.service.ask(user, question, thread_key=f"{channel}:{thread_ts}", channel=channel)
            text = result.answer.text or "(回答が空でした)"
            if result.answer.is_error:
                text = f":warning: {text}"
            if self.settings.show_cost_footer:
                cost = "不明" if result.virtual_cost_usd is None else f"${result.virtual_cost_usd:.3f}"
                text += f"\n\n_{result.answer.num_turns} 往復 / 仮想料金 {cost} / {self.settings.auth_mode}_"
        except NotAllowedError:
            text = NOT_ALLOWED_MESSAGE
        except Exception:
            log.exception("質問の処理に失敗しました")
            text = ":warning: 回答の生成に失敗しました。時間をおいてもう一度試してください。"

        chunks = split_text(text)
        await client.chat_update(channel=channel, ts=placeholder["ts"], text=chunks[0])
        for chunk in chunks[1:]:
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk)

    async def handle_cost_command(self, command: dict[str, Any]) -> str:
        if not self.settings.is_admin(command.get("user_id", "")):
            return "このコマンドは管理者だけが使えます。"
        month = (command.get("text") or "").strip() or None
        return format_summary(summarize(self.service.store, self.prices, month))


def build_app(settings: Settings, service: QAService, prices: PriceTable):
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=settings.slack_bot_token)
    handlers = SlackHandlers(settings, service, prices)

    @app.event("app_mention")
    async def on_mention(event, client):
        await handlers.handle_question(event, client)

    @app.event("message")
    async def on_message(event, client):
        # DM だけに反応する。チャンネルではメンションされたときだけ答える
        if event.get("channel_type") == "im":
            await handlers.handle_question(event, client)

    @app.command("/qa-cost")
    async def on_cost(ack, command, respond):
        await ack()
        await respond(response_type="ephemeral", text=await handlers.handle_cost_command(command))

    return app


async def run_socket_mode(settings: Settings, service: QAService, prices: PriceTable) -> None:
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    app = build_app(settings, service, prices)
    await AsyncSocketModeHandler(app, settings.slack_app_token).start_async()
