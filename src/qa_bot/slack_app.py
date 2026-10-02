"""Slack の受け口(DM、チャンネルでのメンション、AI アプリのパネル、評価ボタン、/qa-cost)

処理の本体は SlackHandlers に分け、Slack に接続しなくてもテストできるようにしている。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from . import attachments as att
from .config import Settings
from .pricing import PriceTable
from .report import format_summary, summarize
from .slack_groups import REFRESH_SECONDS
from .service import DailyLimitError, NotAllowedError, QAService, ServiceAnswer

log = logging.getLogger(__name__)

MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
# Slack の section ブロック1つに入る文字数の上限(3000)に余裕を持たせる
SECTION_CHARS = 2900
MAX_SECTIONS = 40

NOT_ALLOWED_MESSAGE = "このボットは試験運用中のため、利用できるメンバーを限定しています。"
EXTERNAL_CHANNEL_MESSAGE = (
    "社外の方が参加しているチャンネルでは、社内向けの情報を含むためお答えできません。"
    "ボットへの DM で質問してください。"
)
REASON_RETRY_MESSAGE = ("👎 の評価は記録しましたが、理由の入力欄を開けませんでした。"
                        "理由も書く場合は、もう一度 👎 を押してください。")
CHANNEL_NOT_ALLOWED_MESSAGE = "このチャンネルではお答えできません。ボットへの DM で質問してください。"
# 文字を書かずにファイルだけを送られたときの質問
FILES_ONLY_QUESTION = "添付したファイルの内容から、何が起きているか、どう対処すればよいかを教えてください。"
# 文脈として読む、質問より前の発言の範囲(スレッドの外でメンションされたとき)
CONTEXT_WINDOW_SECONDS = 60 * 60
CONTEXT_MAX_CHARS = 6000
THINKING_STATUS = "リポジトリを調べています…"
LOADING_MESSAGES = ["ファイルを探しています…", "コードを読んでいます…", "回答をまとめています…"]
GREETING = (
    "HACARUS Check 2025 のリポジトリについて質問できます。\n"
    "バージョンを指定するときは「v3.2.1 で…」のように書いてください。指定がなければ最新の正式リリースをもとに答えます。"
)
SUGGESTED_PROMPTS = [
    {"title": "最新版の機能を確認する", "message": "最新の正式リリースで、検査結果を CSV で出力できますか？"},
    {"title": "バージョン間の違いを調べる", "message": "v3.2.1 から v3.3.2 に上げるとき、設定やデータの移行は必要ですか？"},
    {"title": "リリース内容を確認する", "message": "v3.3.2 で何が変わりましたか？"},
]

ACTION_GOOD = "qa_good"
ACTION_BAD = "qa_bad"
VIEW_BAD_REASON = "qa_bad_reason"


def strip_mention(text: str) -> str:
    return MENTION_RE.sub("", text or "").strip()


def to_mrkdwn(text: str) -> str:
    """Claude が書く Markdown を、Slack の mrkdwn に寄せる"""
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append("```")
            continue
        if in_code:
            out.append(line)
            continue
        line = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"__(.+?)__", r"*\1*", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        out.append(line)
    return "\n".join(out)


def split_text(text: str, size: int = SECTION_CHARS) -> list[str]:
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
    return chunks or [""]


def skipped_note(skipped: list[att.Skipped]) -> str | None:
    if not skipped:
        return None
    return "📎 読めなかった添付: " + "、".join(f"{s.name}({s.reason})" for s in skipped)


def answer_blocks(text: str, footer: str | None, question_id: int | None,
                  note: str | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in split_text(to_mrkdwn(text))[:MAX_SECTIONS]
    ]
    if note:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
    if footer:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    if question_id is not None:
        blocks.append({
            "type": "actions",
            "block_id": f"feedback-{question_id}",
            "elements": [
                {"type": "button", "action_id": ACTION_GOOD, "value": str(question_id),
                 "text": {"type": "plain_text", "text": "👍 役に立った"}},
                {"type": "button", "action_id": ACTION_BAD, "value": str(question_id),
                 "text": {"type": "plain_text", "text": "👎 違う・足りない"}},
            ],
        })
    return blocks


def bad_reason_view(question_id: int) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": VIEW_BAD_REASON,
        "private_metadata": str(question_id),
        "title": {"type": "plain_text", "text": "評価ありがとうございます"},
        "submit": {"type": "plain_text", "text": "送る"},
        "close": {"type": "plain_text", "text": "送らずに閉じる"},
        "blocks": [{
            "type": "input",
            "block_id": "reason",
            "optional": True,
            "label": {"type": "plain_text", "text": "どこが違った・足りなかったか(任意)"},
            "element": {"type": "plain_text_input", "action_id": "text", "multiline": True, "max_length": 500},
        }],
    }


def slack_user_label(user: str) -> str:
    """Slack ID は <@ID> と書くと、Slack の画面で名前に置き換わる(users:read の権限は要らない)"""
    return f"<@{user}>" if user.startswith(("U", "W")) else user


class SlackHandlers:
    def __init__(self, settings: Settings, service: QAService, prices: PriceTable,
                 fetch: att.Fetcher | None = None):
        self.settings = settings
        self.service = service
        self.prices = prices
        # 添付ファイルを Slack から取得する(テストでは差し替える)
        self.fetch = fetch or (att.slack_fetcher(settings.slack_bot_token) if settings.slack_bot_token else None)

    # ----- 回答を作る共通部分 -----

    def _footer(self, result: ServiceAnswer, in_channel: bool) -> str | None:
        mode = self.settings.cost_footer
        if mode == "never" or (mode == "dm" and in_channel):
            return None
        cost = "不明" if result.virtual_cost_usd is None else f"${result.virtual_cost_usd:.3f}"
        return f"{result.answer.num_turns} 往復 / 仮想料金 {cost} / {self.settings.auth_mode}"

    async def _load_files(self, client: Any, files: list[dict[str, Any]]) -> tuple[list[att.Attachment], list[att.Skipped]]:
        if not files:
            return [], []
        if self.fetch is None:
            return [], [att.Skipped(f.get("name") or "(名前なし)", "ファイルを取得する設定がない") for f in files]
        full: list[dict[str, Any]] = []
        for f in files:
            # 大きなイベントでは、ファイルの情報が省かれて届くことがある
            if f.get("file_access") == "check_file_info" and f.get("id"):
                try:
                    f = (await client.files_info(file=f["id"]))["file"]
                except Exception:
                    log.warning("ファイル %s の情報を読めませんでした", f.get("id"), exc_info=True)
            full.append(f)
        return await att.load(full, self.fetch)

    async def _answer(self, user: str, question: str, thread_key: str, channel: str,
                      in_channel: bool = False, context: str | None = None,
                      attachments: list[att.Attachment] | None = None,
                      note: str | None = None) -> tuple[str, list | None]:
        """利用者に返すテキストと Block Kit のブロックを作る"""
        try:
            result = await self.service.ask(user, question, thread_key=thread_key, channel=channel, context=context,
                                            attachments=attachments)
        except NotAllowedError:
            return NOT_ALLOWED_MESSAGE, None
        except DailyLimitError as e:
            return f"今日の質問の上限({e.limit} 件)に達しました。明日また質問してください。", None
        except Exception:
            log.exception("質問の処理に失敗しました")
            return ":warning: 回答の生成に失敗しました。時間をおいてもう一度試してください。", None

        text = result.answer.text or "(回答が空でした)"
        if result.answer.is_error:
            text = f":warning: {text}"
        return text[:3000], answer_blocks(text, self._footer(result, in_channel), result.question_id, note)

    # ----- AI アプリのパネル -----

    async def thread_started(self, say: Callable[..., Awaitable[Any]], set_suggested_prompts: Callable[..., Awaitable[Any]]) -> None:
        await say(GREETING)
        await set_suggested_prompts(prompts=SUGGESTED_PROMPTS)

    async def assistant_message(
        self,
        payload: dict[str, Any],
        say: Callable[..., Awaitable[Any]],
        set_status: Callable[..., Awaitable[Any]],
        set_title: Callable[..., Awaitable[Any]],
        client: Any = None,
    ) -> None:
        user = payload.get("user", "")
        question = (payload.get("text") or "").strip()
        files = payload.get("files") or []
        channel = payload["channel"]
        thread_ts = payload.get("thread_ts") or payload["ts"]
        if not self.service.is_allowed(user):
            await say(NOT_ALLOWED_MESSAGE)
            return
        if not question and not files:
            await say("質問を書いてください。")
            return
        question = question or FILES_ONLY_QUESTION
        thread_key = f"{channel}:{thread_ts}"
        if self.service.store.get_session(thread_key) is None:
            # 会話の一覧で見分けやすいよう、最初の質問をタイトルにする
            await set_title(question[:60])
        await set_status(THINKING_STATUS, loading_messages=LOADING_MESSAGES)
        loaded, skipped = await self._load_files(client, files)
        text, blocks = await self._answer(user, question, thread_key, channel, attachments=loaded,
                                          note=skipped_note(skipped))
        if blocks:
            await say(text=text, blocks=blocks)
        else:
            await say(text)

    # ----- チャンネルでのメンション -----

    async def handle_mention(self, event: dict[str, Any], client: Any, body: dict[str, Any] | None = None) -> None:
        # ファイルを添付した発言は subtype が file_share になる
        if event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
            return
        user = event.get("user", "")
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        question = strip_mention(event.get("text", ""))
        in_channel = event.get("channel_type") != "im"

        async def reply(text: str) -> None:
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)

        if in_channel:
            # Slack コネクトなどで社外とつながったチャンネルでは答えない
            if (body or {}).get("is_ext_shared_channel") or await self._is_shared_with_outside(client, channel):
                await reply(EXTERNAL_CHANNEL_MESSAGE)
                return
            allowed = self.settings.allowed_channels
            if allowed is not None and channel not in allowed:
                await reply(CHANNEL_NOT_ALLOWED_MESSAGE)
                return
        if not self.service.is_allowed(user):
            await reply(NOT_ALLOWED_MESSAGE)
            return
        files = list(event.get("files") or [])
        if not question and not files:
            await reply("質問を書いてください。")
            return
        question = question or FILES_ONLY_QUESTION

        context = None
        if in_channel:
            context, context_files = await self._thread_context(client, event)
            # スレッドに先に貼られたスクリーンショットやログも読む(質問に添付したものを優先する)
            files += context_files
        placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=":mag: " + THINKING_STATUS)
        loaded, skipped = await self._load_files(client, files)
        text, blocks = await self._answer(user, question, f"{channel}:{thread_ts}", channel,
                                          in_channel=in_channel, context=context, attachments=loaded,
                                          note=skipped_note(skipped))
        await client.chat_update(channel=channel, ts=placeholder["ts"], text=text, blocks=blocks or [])

    async def _is_shared_with_outside(self, client: Any, channel: str) -> bool:
        """チャンネルが社外と共有されているかを Slack に問い合わせる。確かめられなければ共有されていないとみなす"""
        try:
            info = (await client.conversations_info(channel=channel))["channel"]
        except Exception:
            return False
        return bool(info.get("is_ext_shared") or info.get("is_pending_ext_shared"))

    async def _thread_context(self, client: Any, event: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
        """メンションされた発言より前の、人どうしの発言と添付ファイルを読む(ボットがまだ見ていない分だけ)"""
        limit = self.settings.thread_context_messages
        if limit <= 0:
            return None, []
        channel, ts = event["channel"], float(event["ts"])
        try:
            if event.get("thread_ts"):
                resp = await client.conversations_replies(channel=channel, ts=event["thread_ts"], limit=200)
                messages = [m for m in resp.get("messages", []) if float(m["ts"]) < ts]
                # ボットが前に答えたところまでは、会話の記録に入っている
                last_bot = max((i for i, m in enumerate(messages) if m.get("bot_id")), default=-1)
                messages = messages[last_bot + 1:]
            else:
                resp = await client.conversations_history(channel=channel, latest=event["ts"], inclusive=False,
                                                          limit=limit)
                messages = [m for m in reversed(resp.get("messages", []))
                            if float(m["ts"]) >= ts - CONTEXT_WINDOW_SECONDS]
        except Exception:
            log.warning("スレッドの発言を読めませんでした(権限が足りない可能性があります)", exc_info=True)
            return None, []
        human = [m for m in messages if not m.get("bot_id") and m.get("subtype") in (None, "file_share")][-limit:]
        lines = [f"<@{m.get('user', '?')}>: {m['text']}" for m in human if m.get("text")]
        # 新しい発言の添付から先に読む
        files = [f for m in reversed(human) for f in m.get("files") or []]
        text = "\n".join(lines)
        return text[-CONTEXT_MAX_CHARS:] or None, files

    # ----- 評価 -----

    async def handle_feedback(self, body: dict[str, Any], client: Any) -> None:
        action = body["actions"][0]
        user = body["user"]["id"]
        if not self.service.is_allowed(user):
            return
        qid = int(action["value"])
        good = action["action_id"] == ACTION_GOOD
        if not good:
            # trigger_id の期限はボタンを押してから3秒なので、ほかの処理より先に入力欄を開く
            try:
                await client.views_open(trigger_id=body["trigger_id"], view=bad_reason_view(qid))
            except Exception:
                log.warning("理由の入力欄を開けませんでした(質問 %d)", qid, exc_info=True)
                self.service.rate(qid, user, -1)
                # もう一度押せるよう、ボタンは残す
                await client.chat_postEphemeral(channel=body["channel"]["id"], user=user, text=REASON_RETRY_MESSAGE)
                return
        self.service.rate(qid, user, 1 if good else -1)

        # 押したことが分かるよう、ボタンを評価の結果に置き換える
        message = body.get("message") or {}
        blocks = [b for b in message.get("blocks", []) if b.get("block_id") != f"feedback-{qid}"]
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "👍 評価ありがとうございます" if good
                                                         else "👎 評価ありがとうございます。改善に使います"}]})
        await client.chat_update(channel=body["channel"]["id"], ts=message.get("ts"),
                                 text=message.get("text", ""), blocks=blocks)

    async def handle_bad_reason(self, body: dict[str, Any]) -> None:
        view = body["view"]
        reason = (((view.get("state") or {}).get("values") or {}).get("reason") or {}).get("text", {}).get("value")
        if reason and reason.strip():
            self.service.store.set_feedback_reason(int(view["private_metadata"]), body["user"]["id"], reason.strip())

    # ----- 管理者向け -----

    async def handle_cost_command(self, command: dict[str, Any]) -> str:
        if not self.service.is_admin(command.get("user_id", "")):
            return "このコマンドは管理者だけが使えます。"
        month = (command.get("text") or "").strip() or None
        return format_summary(summarize(self.service.store, self.prices, month), user_label=slack_user_label)


def build_app(settings: Settings, service: QAService, prices: PriceTable):
    from slack_bolt.async_app import AsyncApp, AsyncAssistant

    app = AsyncApp(token=settings.slack_bot_token)
    handlers = SlackHandlers(settings, service, prices)

    if settings.slack_assistant:
        # AI アプリのパネルを使うときだけ登録する。登録すると、DM のスレッドでの返信もパネル側で受け取る
        assistant = AsyncAssistant()

        @assistant.thread_started
        async def on_thread_started(say, set_suggested_prompts):
            await handlers.thread_started(say, set_suggested_prompts)

        @assistant.user_message
        async def on_user_message(payload, say, set_status, set_title, client):
            await handlers.assistant_message(payload, say, set_status, set_title, client)

        app.use(assistant)

    @app.event("app_mention")
    async def on_mention(event, client, body):
        await handlers.handle_mention(event, client, body)

    @app.event("message")
    async def on_message(event, client, body):
        # ボットへの DM に答える(チャンネルの発言はメンションされたときだけ app_mention で受け取る)
        if event.get("channel_type") == "im":
            await handlers.handle_mention(event, client, body)

    @app.action(ACTION_GOOD)
    @app.action(ACTION_BAD)
    async def on_feedback(ack, body, client):
        await ack()
        await handlers.handle_feedback(body, client)

    @app.view(VIEW_BAD_REASON)
    async def on_bad_reason(ack, body):
        await ack()
        await handlers.handle_bad_reason(body)

    @app.command("/qa-cost")
    async def on_cost(ack, command, respond):
        await ack()
        await respond(response_type="ephemeral", text=await handlers.handle_cost_command(command))

    return app


async def _purge_daily(service: QAService) -> None:
    while True:
        try:
            n = service.purge()
            log.info("保存期間を過ぎた記録を整理しました(セッション %d 件)", n)
        except Exception:
            log.exception("記録の整理に失敗しました")
        await asyncio.sleep(24 * 60 * 60)


async def _refresh_groups(service: QAService, client: Any) -> None:
    while True:
        await service.refresh_groups(client)
        await asyncio.sleep(REFRESH_SECONDS)


async def run_socket_mode(settings: Settings, service: QAService, prices: PriceTable) -> None:
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    app = build_app(settings, service, prices)
    # 起動してすぐの質問にも答えられるよう、先にユーザーグループのメンバーを読む
    await service.refresh_groups(app.client)
    tasks = [asyncio.create_task(_purge_daily(service)), asyncio.create_task(_refresh_groups(service, app.client))]
    try:
        await AsyncSocketModeHandler(app, settings.slack_app_token).start_async()
    finally:
        for t in tasks:
            t.cancel()
