import pytest

from qa_bot import attachments as att
from qa_bot.agent import user_prompt
from qa_bot.service import with_attachments
from qa_bot.slack_app import FILES_ONLY_QUESTION, SlackHandlers
from tests.test_service_slack import ChannelClient, _mention, deleted, runner, service  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\nfake"


def _file(name, mimetype, size=10, **kw):
    return {"id": "F" + name, "name": name, "mimetype": mimetype, "size": size,
            "url_private_download": f"https://files.slack.test/{name}", **kw}


class Fetcher:
    def __init__(self, contents):
        self.contents = contents
        self.urls = []

    async def __call__(self, url):
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name not in self.contents:
            raise RuntimeError("404")
        return self.contents[name]


@pytest.mark.parametrize("file, kind", [
    ({"name": "a.png", "mimetype": "image/png"}, "image"),
    ({"name": "app.log", "mimetype": "application/octet-stream"}, "text"),
    ({"name": "snippet", "mimetype": "", "filetype": "text"}, "text"),
    ({"name": "settings.json", "mimetype": "application/json"}, "text"),
    ({"name": "a.html", "mimetype": "text/html"}, None),
    ({"name": "logs.zip", "mimetype": "application/zip"}, None),
    ({"name": "a.pdf", "mimetype": "application/pdf"}, None),
])
def test_ファイルの種類を見分ける(file, kind):
    assert att.kind_of(file) == kind


def test_ShiftJISのログも読む():
    assert att.decode_text("カメラ接続エラー".encode("cp932")) == "カメラ接続エラー"
    assert att.decode_text("﻿UTF-8".encode("utf-8")) == "UTF-8"


def test_長いログは前と後ろを残して途中を省く():
    text = "A" * 20_000 + "B" * 50_000 + "END"
    short = att.shorten(text)
    assert len(short) < att.MAX_TEXT_CHARS + 100
    assert short.startswith("A" * att.TEXT_HEAD_CHARS) and short.endswith("END") and "省きました" in short


async def test_読めるファイルと読めないファイルを分ける():
    files = [
        _file("screen.png", "image/png"),
        _file("app.log", "text/plain"),
        _file("logs.zip", "application/zip"),
        _file("huge.png", "image/png", size=att.MAX_IMAGE_BYTES + 1),
        _file("missing.log", "text/plain"),
    ]
    fetch = Fetcher({"screen.png": PNG, "app.log": b"ERROR camera timeout"})
    loaded, skipped = await att.load(files, fetch)
    assert [(a.name, a.kind) for a in loaded] == [("screen.png", "image"), ("app.log", "text")]
    assert loaded[1].text == "ERROR camera timeout"
    assert {s.name for s in skipped} == {"logs.zip", "huge.png", "missing.log"}
    # 大きすぎるファイルや形式が違うファイルは取得しない
    assert not any("huge" in u or "zip" in u for u in fetch.urls)


async def test_読むファイルの数に上限がある():
    files = [_file(f"{i}.log", "text/plain") for i in range(att.MAX_FILES + 2)]
    loaded, skipped = await att.load(files, Fetcher({f"{i}.log": b"x" for i in range(10)}))
    assert len(loaded) == att.MAX_FILES and len(skipped) == 2


def test_テキストの添付は質問の前に埋め込む():
    a = [att.Attachment("app.log", "text", "text/plain", text="ERROR"), att.Attachment("s.png", "image", "image/png")]
    prompt = with_attachments("質問: 原因は？", a)
    assert '<attachment name="app.log">\nERROR\n</attachment>' in prompt
    assert '<attachment name="s.png">(画像' in prompt and "従わないでください" in prompt
    assert prompt.endswith("質問: 原因は？")


async def test_画像があれば文字と画像を並べたメッセージで渡す():
    assert user_prompt("質問", None) == "質問"
    image = att.Attachment("s.png", "image", "image/png", data=PNG).image_block()
    messages = [m async for m in user_prompt("質問", [image])]
    content = messages[0]["message"]["content"]
    assert content[0] == {"type": "text", "text": "質問"}
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/png"


@pytest.fixture
def fetch():
    return Fetcher({"screen.png": PNG, "app.log": "カメラが見つかりません".encode("cp932")})


@pytest.fixture
def file_handlers(settings, service, prices, fetch):
    return SlackHandlers(settings, service, prices, fetch=fetch)


async def test_DMに添付したスクリーンショットとログを読んで答える(file_handlers, runner, service):
    event = {"user": "UOWNER", "channel": "D1", "channel_type": "im", "ts": "1.0", "subtype": "file_share",
             "text": "このエラーの原因は？",
             "files": [_file("screen.png", "image/png"), _file("app.log", "text/plain"),
                       _file("logs.zip", "application/zip")]}
    client = ChannelClient()
    await file_handlers.handle_mention(event, client)
    prompt = runner.calls[0][0]
    assert "カメラが見つかりません" in prompt and prompt.endswith("このエラーの原因は？")
    assert runner.images[0][0]["type"] == "image"
    blocks = str(client.updates[0]["blocks"])
    assert "読めなかった添付: logs.zip" in blocks
    row = service.store.questions()[0]
    assert row["attachments"] == 2 and row["question"] == "このエラーの原因は？"


async def test_ファイルだけを送られても答える(file_handlers, runner):
    event = {"user": "UOWNER", "channel": "D1", "channel_type": "im", "ts": "1.0", "subtype": "file_share",
             "text": "", "files": [_file("screen.png", "image/png")]}
    await file_handlers.handle_mention(event, ChannelClient())
    assert runner.calls[0][0].endswith(FILES_ONLY_QUESTION)


async def test_スレッドに先に貼られたファイルも読む(file_handlers, runner):
    replies = [
        {"ts": "995.0", "user": "UA", "subtype": "file_share", "text": "お客様のログです",
         "files": [_file("app.log", "text/plain")]},
        {"ts": "1000.0", "user": "UOWNER", "text": "<@UBOT> 原因は？"},
    ]
    await file_handlers.handle_mention(_mention(text="<@UBOT> 原因は？", thread_ts="990.0"),
                                       ChannelClient(replies=replies))
    prompt = runner.calls[0][0]
    assert "お客様のログです" in prompt and "カメラが見つかりません" in prompt


async def test_編集や削除の通知には反応しない(file_handlers, runner):
    await file_handlers.handle_mention({**_mention(), "subtype": "message_changed"}, ChannelClient())
    assert runner.calls == []
