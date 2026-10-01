from datetime import datetime, timedelta, timezone

import pytest

from qa_bot.agent import AgentAnswer, FakeAgentRunner
from qa_bot.config import CLI_USER_ID, load_settings
from qa_bot.service import DailyLimitError, NotAllowedError, QAService
from qa_bot.slack_app import (
    ACTION_BAD, ACTION_GOOD, NOT_ALLOWED_MESSAGE, SUGGESTED_PROMPTS, SlackHandlers, answer_blocks, split_text,
    strip_mention, to_mrkdwn,
)
from qa_bot.store import Store
from tests.test_pricing_report import record


@pytest.fixture
def runner(settings):
    return FakeAgentRunner(settings.model)


@pytest.fixture
def deleted():
    return []


@pytest.fixture
def service(settings, runner, prices, deleted):
    return QAService(settings, runner, Store(settings.db_path), prices, session_deleter=deleted.append)


@pytest.fixture
def handlers(settings, service, prices):
    return SlackHandlers(settings, service, prices)


class FakeSlackClient:
    def __init__(self):
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.views: list[dict] = []

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ts": f"reply-{len(self.posts)}"}

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}

    async def views_open(self, **kwargs):
        self.views.append(kwargs)
        return {"ok": True}


class Recorder:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


# ----- QAService -----

async def test_質問と回答を記録し仮想料金を計算する(service, prices):
    result = await service.ask(CLI_USER_ID, "検査の閾値はどこで設定する？")
    assert result.virtual_cost_usd == pytest.approx(
        prices.cost("claude-sonnet-5-5", result.answer.model_usage["claude-sonnet-5-5"])
    )
    row = service.store.questions()[0]
    assert row["auth_mode"] == "subscription"
    assert row["question"] == "検査の閾値はどこで設定する？" and row["answer"].startswith("(ダミー回答")


async def test_同じスレッドでは前のセッションを再開する(service, runner):
    first = await service.ask(CLI_USER_ID, "1つめ", thread_key="t1")
    second = await service.ask(CLI_USER_ID, "2つめ", thread_key="t1")
    await service.ask(CLI_USER_ID, "別スレッド", thread_key="t2")
    assert runner.calls[0][1] is None
    assert runner.calls[1][1] == first.answer.session_id
    assert second.resumed
    assert runner.calls[2][1] is None


async def test_再開に失敗したら新しい会話として答える(service, runner):
    await service.ask(CLI_USER_ID, "1つめ", thread_key="t1")
    original = runner.ask

    async def flaky(question, resume_session_id=None, images=None):
        if resume_session_id:
            raise RuntimeError("session not found")
        return await original(question, resume_session_id)

    runner.ask = flaky
    assert not (await service.ask(CLI_USER_ID, "2つめ", thread_key="t1")).resumed


async def test_許可されていない利用者は質問できない(service):
    with pytest.raises(NotAllowedError):
        await service.ask("UOTHER", "質問")
    assert service.store.questions() == []


async def test_1人1日の上限を超えると断る(base_env, runner, prices):
    base_env["DAILY_LIMIT_PER_USER"] = "2"
    s = load_settings(base_env)
    svc = QAService(s, runner, Store(s.db_path), prices)
    await svc.ask("UOWNER", "1")
    await svc.ask("UOWNER", "2")
    with pytest.raises(DailyLimitError):
        await svc.ask("UOWNER", "3")
    # CLI(管理者本人)には上限をかけない
    await svc.ask(CLI_USER_ID, "4")


def test_日付の区切りはタイムゾーンで決める(settings, tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    # 日本時間 0:30 は UTC では前日の 15:30
    store.add_question(record("U", 0.1, asked_at=datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc)))
    now = datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc)
    assert store.count_today("U", settings.timezone, now) == 1
    assert store.count_today("U", timezone.utc, now) == 1
    assert store.count_today("U", settings.timezone, now + timedelta(days=1)) == 0


async def test_質問文を記録しない設定(base_env, runner, prices):
    base_env["LOG_QUESTION_TEXT"] = "false"
    s = load_settings(base_env)
    svc = QAService(s, runner, Store(s.db_path), prices)
    await svc.ask(CLI_USER_ID, "秘密の質問")
    row = svc.store.questions()[0]
    assert row["question"] is None and row["answer"] is None


def test_保存期間を過ぎた記録とセッションを消す(service, deleted):
    store = service.store
    now = datetime.now(timezone.utc)
    store.add_question(record("U", 0.1, session_id="old-session", asked_at=now - timedelta(days=40)))
    store.add_question(record("U", 0.1, session_id="ancient", asked_at=now - timedelta(days=400)))
    store.add_question(record("U", 0.1, session_id="active", asked_at=now - timedelta(days=40)))
    store.set_session("thread-active", "active")  # 40 日前に始まったが、いまも続いている会話
    assert service.purge() == 2
    assert sorted(deleted) == ["ancient", "old-session"]
    assert len(store.questions()) == 2


# ----- Slack の AI アプリのパネル -----

async def test_会話を始めると質問例を出す(handlers):
    say, prompts = Recorder(), Recorder()
    await handlers.thread_started(say, prompts)
    assert "バージョン" in say.calls[0][0][0]
    assert prompts.calls[0][1]["prompts"] == SUGGESTED_PROMPTS


async def test_パネルの質問に答えて評価ボタンを付ける(handlers, service):
    say, status, title = Recorder(), Recorder(), Recorder()
    payload = {"user": "UOWNER", "channel": "D1", "thread_ts": "10.0", "ts": "10.1", "text": "v1.0.0 のカメラの台数は？"}
    await handlers.assistant_message(payload, say, status, title)
    assert title.calls[0][0][0] == "v1.0.0 のカメラの台数は？"
    assert status.calls
    blocks = say.calls[0][1]["blocks"]
    assert {e["action_id"] for e in blocks[-1]["elements"]} == {ACTION_GOOD, ACTION_BAD}
    assert service.store.questions()[0]["thread_key"] == "D1:10.0"

    # 2つめの質問ではタイトルを付け直さない
    await handlers.assistant_message({**payload, "ts": "10.2", "text": "続き"}, say, status, title)
    assert len(title.calls) == 1


async def test_パネルでも許可されていない人には断る(handlers, service):
    say = Recorder()
    await handlers.assistant_message({"user": "UOTHER", "channel": "D1", "ts": "1", "text": "x"}, say, Recorder(), Recorder())
    assert say.calls[0][0][0] == NOT_ALLOWED_MESSAGE
    assert service.store.questions() == []


# ----- チャンネルでのメンション -----

async def test_Slackのメンションに答える(handlers, service):
    client = FakeSlackClient()
    await handlers.handle_mention({"user": "UOWNER", "channel": "C1", "ts": "100.1", "text": "<@UBOT> 起動手順は？"}, client)
    assert client.posts[0]["thread_ts"] == "100.1"
    assert "起動手順は？" in client.updates[0]["text"]
    assert service.store.questions()[0]["thread_key"] == "C1:100.1"


async def test_Slackで許可されていない人には断る(handlers, service):
    client = FakeSlackClient()
    await handlers.handle_mention({"user": "UOTHER", "channel": "C1", "ts": "1", "text": "<@UBOT> 質問"}, client)
    assert client.posts == [{"channel": "C1", "thread_ts": "1", "text": NOT_ALLOWED_MESSAGE}]


async def test_ボット自身の投稿には反応しない(handlers):
    client = FakeSlackClient()
    await handlers.handle_mention({"bot_id": "B1", "user": "UOWNER", "channel": "C1", "ts": "1", "text": "x"}, client)
    assert client.posts == []


async def test_上限に達したら案内する(base_env, runner, prices):
    base_env["DAILY_LIMIT_PER_USER"] = "1"
    s = load_settings(base_env)
    h = SlackHandlers(s, QAService(s, runner, Store(s.db_path), prices), prices)
    client = FakeSlackClient()
    for ts in ("1", "2"):
        await h.handle_mention({"user": "UOWNER", "channel": "C1", "ts": ts, "text": "<@UBOT> 質問"}, client)
    assert "上限" in client.updates[1]["text"]


async def test_長い回答は複数のブロックに分ける(handlers, runner):
    async def long_answer(question, resume_session_id=None, images=None):
        return AgentAnswer(text=("あ" * 100 + "\n") * 80, session_id="s")

    runner.ask = long_answer
    say = Recorder()
    await handlers.assistant_message({"user": "UOWNER", "channel": "D1", "ts": "1", "text": "長く"}, say, Recorder(), Recorder())
    sections = [b for b in say.calls[0][1]["blocks"] if b["type"] == "section"]
    assert len(sections) >= 3 and all(len(b["text"]["text"]) <= 3000 for b in sections)


# ----- 評価 -----

def _action_body(action_id: str, qid: int, user: str = "UOWNER") -> dict:
    return {
        "actions": [{"action_id": action_id, "value": str(qid)}],
        "user": {"id": user}, "trigger_id": "trig", "channel": {"id": "D1"},
        "message": {"ts": "9.9", "text": "回答", "blocks": answer_blocks("回答", None, qid)},
    }


async def test_良い評価を記録しボタンを消す(handlers, service):
    qid = (await service.ask(CLI_USER_ID, "質問")).question_id
    client = FakeSlackClient()
    await handlers.handle_feedback(_action_body(ACTION_GOOD, qid), client)
    assert service.store.feedback_rows()[0]["rating"] == 1
    assert client.views == []
    assert all(b["type"] != "actions" for b in client.updates[0]["blocks"])


async def test_悪い評価では理由を聞き任意で記録する(handlers, service):
    qid = (await service.ask(CLI_USER_ID, "質問")).question_id
    client = FakeSlackClient()
    await handlers.handle_feedback(_action_body(ACTION_BAD, qid), client)
    assert service.store.feedback_rows()[0]["rating"] == -1
    view = client.views[0]["view"]
    assert view["private_metadata"] == str(qid)

    await handlers.handle_bad_reason({"user": {"id": "UOWNER"}, "view": {
        "private_metadata": str(qid), "state": {"values": {"reason": {"text": {"value": " 古い版の説明だった "}}}}}})
    assert service.store.feedback_rows()[0]["reason"] == "古い版の説明だった"


async def test_許可されていない人の評価は記録しない(handlers, service):
    qid = (await service.ask(CLI_USER_ID, "質問")).question_id
    await handlers.handle_feedback(_action_body(ACTION_GOOD, qid, user="UOTHER"), FakeSlackClient())
    assert service.store.feedback_rows() == []


async def test_qa_costは管理者だけが使える(handlers, service):
    await service.ask(CLI_USER_ID, "質問")
    assert "仮想料金レポート" in await handlers.handle_cost_command({"user_id": "UOWNER", "text": ""})
    assert "管理者だけ" in await handlers.handle_cost_command({"user_id": "UOTHER", "text": ""})


# ----- 文字列の整形 -----

def test_メンションを取り除く():
    assert strip_mention("<@U123ABC> こんにちは <@U999>") == "こんにちは"


def test_分割は改行の位置で行う():
    assert split_text("a" * 10 + "\n" + "b" * 10, size=15) == ["a" * 10, "b" * 10]


def test_MarkdownをSlackの書式に寄せる():
    md = "## 結論\n**太字** と [資料](https://example.com)\n- 項目\n```\n**そのまま**\n```"
    assert to_mrkdwn(md) == "*結論*\n*太字* と <https://example.com|資料>\n• 項目\n```\n**そのまま**\n```"


# ----- 会話を続けたときのトークン数 -----

def test_セッションの合計から前回分を引く():
    from qa_bot.pricing import TokenUsage
    from qa_bot.service import per_question_usage

    cur = {"m": TokenUsage(30, 300, 3000, 30000), "h": TokenUsage(900, 10, 0, 0)}
    prev = {"m": TokenUsage(10, 100, 1000, 10000), "h": TokenUsage(900, 10, 0, 0)}
    usage, cost = per_question_usage(cur, 0.9, prev, 0.4)
    assert usage == {"m": TokenUsage(20, 200, 2000, 20000)}
    assert cost == pytest.approx(0.5)
    # 減っていれば合計ではなかったとみなし、そのまま使う
    assert per_question_usage(prev, 0.4, cur, 0.9) == (prev, 0.4)


async def test_会話を続けても1件ごとの料金を記録する(service, prices):
    first = await service.ask(CLI_USER_ID, "1つめ", thread_key="t1")
    second = await service.ask(CLI_USER_ID, "2つめ", thread_key="t1")
    third = await service.ask(CLI_USER_ID, "3つめ", thread_key="t1")
    assert first.virtual_cost_usd == pytest.approx(second.virtual_cost_usd)
    assert second.virtual_cost_usd == pytest.approx(third.virtual_cost_usd)
    rows = service.store.model_rows()
    assert {r["output_tokens"] for r in rows} == {FakeAgentRunner.PER_QUESTION.output}


def test_以前の版の合計で記録した行を1件ごとに直す(tmp_path):
    from qa_bot.pricing import TokenUsage

    path = tmp_path / "old.sqlite3"
    store = Store(path)
    store.conn.execute("DELETE FROM meta")  # 補正前の記録ファイルを再現する
    for i, resumed in enumerate([False, True, True], start=1):
        store.add_question(record("U", 0.1 * i, session_id="s1", resumed=resumed,
                                  model_usage={"claude-sonnet-5-5": (TokenUsage(i, 10 * i, 100 * i, 1000 * i), 0.1 * i)}))
    store.add_question(record("U", 0.05, session_id="s2", resumed=False,
                              model_usage={"claude-sonnet-5-5": (TokenUsage(5, 5, 5, 5), 0.05)}))
    store.conn.commit()
    store.close()

    fixed = Store(path)
    costs = [r["virtual_cost_usd"] for r in fixed.questions()]
    assert costs == pytest.approx([0.1, 0.1, 0.1, 0.05])
    rows = sorted(fixed.model_rows(), key=lambda r: r["question_id"])
    assert [r["output_tokens"] for r in rows] == [10, 10, 10, 5]
    assert fixed.get_session_totals("s1")[0]["claude-sonnet-5-5"] == TokenUsage(3, 30, 300, 3000)
    # 2回目以降は何もしない
    fixed.close()
    assert [r["virtual_cost_usd"] for r in Store(path).questions()] == pytest.approx([0.1, 0.1, 0.1, 0.05])


# ----- チャンネルでの制限と文脈 -----

class ChannelClient(FakeSlackClient):
    def __init__(self, replies=None, history=None, ext_shared=False):
        super().__init__()
        self.replies, self.history, self.ext_shared = replies or [], history or [], ext_shared

    async def conversations_info(self, channel):
        return {"channel": {"id": channel, "is_ext_shared": self.ext_shared}}

    async def conversations_replies(self, channel, ts, limit):
        return {"messages": self.replies}

    async def conversations_history(self, channel, latest, inclusive, limit):
        return {"messages": self.history}


def _mention(text="<@UBOT> これって v1.0.0 でもできる？", **kw):
    return {"user": "UOWNER", "channel": "C1", "channel_type": "channel", "ts": "1000.0", "text": text, **kw}


@pytest.mark.parametrize("client, body", [
    (ChannelClient(), {"is_ext_shared_channel": True}),
    (ChannelClient(ext_shared=True), {}),
])
async def test_社外と共有したチャンネルでは答えない(handlers, service, client, body):
    from qa_bot.slack_app import EXTERNAL_CHANNEL_MESSAGE

    await handlers.handle_mention(_mention(), client, body)
    assert client.posts[0]["text"] == EXTERNAL_CHANNEL_MESSAGE
    assert service.store.questions() == []


async def test_許可したチャンネルでだけ答える(base_env, runner, prices):
    from qa_bot.slack_app import CHANNEL_NOT_ALLOWED_MESSAGE

    base_env["ALLOWED_CHANNELS"] = "C9"
    s = load_settings(base_env)
    h = SlackHandlers(s, QAService(s, runner, Store(s.db_path), prices), prices)
    client = ChannelClient()
    await h.handle_mention(_mention(), client)
    assert client.posts[0]["text"] == CHANNEL_NOT_ALLOWED_MESSAGE
    # DM はチャンネルの制限を受けない
    await h.handle_mention({**_mention(), "channel": "D1", "channel_type": "im"}, client)
    assert client.updates


async def test_スレッドでは前回の回答より後の発言を文脈として渡す(handlers, runner, service):
    replies = [
        {"ts": "990.0", "user": "UA", "text": "古い発言"},
        {"ts": "991.0", "bot_id": "B1", "text": "前回の回答"},
        {"ts": "995.0", "user": "UA", "text": "カメラの台数の話です"},
        {"ts": "996.0", "user": "UB", "text": "v1.0.0 のお客様です"},
        {"ts": "1000.0", "user": "UOWNER", "text": "<@UBOT> これって v1.0.0 でもできる？"},
    ]
    await handlers.handle_mention(_mention(thread_ts="990.0"), ChannelClient(replies=replies))
    prompt = runner.calls[0][0]
    assert "<@UA>: カメラの台数の話です" in prompt and "<@UB>: v1.0.0 のお客様です" in prompt
    assert "古い発言" not in prompt and "前回の回答" not in prompt
    assert prompt.endswith("質問: これって v1.0.0 でもできる？")
    # 記録するのは質問だけ
    assert service.store.questions()[0]["question"] == "これって v1.0.0 でもできる？"


async def test_スレッドの外では直近1時間の発言を文脈として渡す(handlers, runner):
    history = [  # Slack は新しい順に返す
        {"ts": "9999.0", "user": "UB", "text": "直前の発言"},
        {"ts": "3000.0", "user": "UA", "text": "2時間近く前の発言"},
    ]
    await handlers.handle_mention(_mention(ts="10000.0"), ChannelClient(history=history))
    prompt = runner.calls[0][0]
    assert "直前の発言" in prompt and "2時間近く前の発言" not in prompt


async def test_発言を読めなくても質問には答える(handlers, runner, service):
    await handlers.handle_mention(_mention(), FakeSlackClient())
    assert runner.calls[0][0] == "これって v1.0.0 でもできる？"


async def test_料金の表示は既定でDMだけ(handlers):
    channel_client, dm_client = ChannelClient(), ChannelClient()
    await handlers.handle_mention(_mention(), channel_client)
    await handlers.handle_mention({**_mention(), "channel": "D1", "channel_type": "im"}, dm_client)
    assert "仮想料金" not in str(channel_client.updates[0]["blocks"])
    assert "仮想料金" in str(dm_client.updates[0]["blocks"])


@pytest.mark.parametrize("env, expected", [
    ({}, "dm"), ({"COST_FOOTER": "always"}, "always"), ({"SHOW_COST_FOOTER": "false"}, "never"),
])
def test_料金の表示場所の設定(base_env, env, expected):
    base_env.update(env)
    assert load_settings(base_env).cost_footer == expected
