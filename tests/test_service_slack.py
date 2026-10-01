import pytest

from qa_bot.agent import AgentAnswer, FakeAgentRunner
from qa_bot.config import CLI_USER_ID
from qa_bot.service import NotAllowedError, QAService
from qa_bot.slack_app import NOT_ALLOWED_MESSAGE, THINKING_MESSAGE, SlackHandlers, split_text, strip_mention
from qa_bot.store import Store


@pytest.fixture
def runner(settings):
    return FakeAgentRunner(settings.model)


@pytest.fixture
def service(settings, runner, prices):
    return QAService(settings, runner, Store(settings.db_path), prices)


class FakeSlackClient:
    def __init__(self):
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ts": f"reply-{len(self.posts)}"}

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}


async def test_質問を記録し仮想料金を計算する(service, prices):
    result = await service.ask(CLI_USER_ID, "検査の閾値はどこで設定する？")
    assert result.virtual_cost_usd == pytest.approx(
        prices.cost("claude-sonnet-5-5", result.answer.model_usage["claude-sonnet-5-5"])
    )
    rows = service.store.questions()
    assert len(rows) == 1
    assert rows[0]["auth_mode"] == "subscription"
    assert rows[0]["question"] == "検査の閾値はどこで設定する？"


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

    async def flaky(question, resume_session_id=None):
        if resume_session_id:
            raise RuntimeError("session not found")
        return await original(question, resume_session_id)

    runner.ask = flaky
    result = await service.ask(CLI_USER_ID, "2つめ", thread_key="t1")
    assert not result.resumed


async def test_許可されていない利用者は質問できない(service):
    with pytest.raises(NotAllowedError):
        await service.ask("UOTHER", "質問")
    assert service.store.questions() == []


async def test_質問文を記録しない設定(base_env, runner, prices):
    from qa_bot.config import load_settings

    base_env["LOG_QUESTION_TEXT"] = "false"
    s = load_settings(base_env)
    svc = QAService(s, runner, Store(s.db_path), prices)
    await svc.ask(CLI_USER_ID, "秘密の質問")
    assert svc.store.questions()[0]["question"] is None


async def test_Slackのメンションに答える(settings, service, prices):
    client = FakeSlackClient()
    handlers = SlackHandlers(settings, service, prices)
    await handlers.handle_question(
        {"user": "UOWNER", "channel": "C1", "ts": "100.1", "text": "<@UBOT> 起動手順は？"}, client
    )
    assert client.posts[0]["text"] == THINKING_MESSAGE
    assert client.posts[0]["thread_ts"] == "100.1"
    assert "起動手順は？" in client.updates[0]["text"]
    assert "仮想料金" in client.updates[0]["text"]
    assert service.store.questions()[0]["thread_key"] == "C1:100.1"


async def test_Slackで許可されていない人には断る(settings, service, prices):
    client = FakeSlackClient()
    await SlackHandlers(settings, service, prices).handle_question(
        {"user": "UOTHER", "channel": "C1", "ts": "1", "text": "<@UBOT> 質問"}, client
    )
    assert client.posts == [{"channel": "C1", "thread_ts": "1", "text": NOT_ALLOWED_MESSAGE}]
    assert service.store.questions() == []


async def test_ボット自身の投稿には反応しない(settings, service, prices):
    client = FakeSlackClient()
    await SlackHandlers(settings, service, prices).handle_question(
        {"bot_id": "B1", "user": "UOWNER", "channel": "C1", "ts": "1", "text": "x"}, client
    )
    assert client.posts == []


async def test_長い回答は分割して投稿する(settings, service, prices, runner):
    async def long_answer(question, resume_session_id=None):
        return AgentAnswer(text=("あ" * 100 + "\n") * 80, session_id="s")

    runner.ask = long_answer
    client = FakeSlackClient()
    await SlackHandlers(settings, service, prices).handle_question(
        {"user": "UOWNER", "channel": "C1", "ts": "1", "text": "<@UBOT> 長く"}, client
    )
    assert len(client.updates) == 1
    assert len(client.posts) >= 2


async def test_qa_costは管理者だけが使える(settings, service, prices):
    handlers = SlackHandlers(settings, service, prices)
    await service.ask(CLI_USER_ID, "質問")
    assert "仮想料金レポート" in await handlers.handle_cost_command({"user_id": "UOWNER", "text": ""})
    assert "管理者だけ" in await handlers.handle_cost_command({"user_id": "UOTHER", "text": ""})


def test_メンションを取り除く():
    assert strip_mention("<@U123ABC> こんにちは <@U999>") == "こんにちは"


def test_分割は改行の位置で行う():
    chunks = split_text("a" * 10 + "\n" + "b" * 10, size=15)
    assert chunks == ["a" * 10, "b" * 10]
