"""Slack に添付されたファイル(スクリーンショットやログ)を読み、Claude に渡せる形にする"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# 1つの質問で読むファイルの数
MAX_FILES = 5
# Claude に送れる画像の大きさの上限(API の上限は 5MB)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# テキストとして読むファイルの大きさの上限と、Claude に渡す文字数の上限
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 40_000
# 長いログは、終わりのほうにエラーが出ていることが多いので、後ろを多めに残す
TEXT_HEAD_CHARS = 10_000

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXTENSIONS = {
    "log", "txt", "csv", "tsv", "json", "xml", "ini", "cfg", "conf", "config", "yaml", "yml", "toml", "md",
}
# Slack のスニペットや、拡張子で判定できるテキスト
TEXT_FILETYPES = TEXT_EXTENSIONS | {"text", "javascript", "python", "csharp", "powershell", "shell", "sql"}

Fetcher = Callable[[str], Awaitable[bytes]]


@dataclass
class Attachment:
    name: str
    kind: str  # image / text
    media_type: str
    data: bytes = b""
    text: str = ""

    def image_block(self) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": self.media_type,
                                            "data": base64.b64encode(self.data).decode("ascii")}}


@dataclass
class Skipped:
    name: str
    reason: str


def kind_of(file: dict[str, Any]) -> str | None:
    mimetype = (file.get("mimetype") or "").lower()
    filetype = (file.get("filetype") or "").lower()
    ext = (file.get("name") or "").rsplit(".", 1)[-1].lower() if "." in (file.get("name") or "") else ""
    if mimetype in IMAGE_TYPES:
        return "image"
    if mimetype == "text/html":
        return None
    if mimetype.startswith("text/") or filetype in TEXT_FILETYPES or ext in TEXT_EXTENSIONS:
        return "text"
    return None


def decode_text(data: bytes) -> str:
    """Windows のログは Shift_JIS のことがあるので、UTF-8 で読めなければ cp932 で読む"""
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def shorten(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    tail = MAX_TEXT_CHARS - TEXT_HEAD_CHARS
    omitted = len(text) - MAX_TEXT_CHARS
    return f"{text[:TEXT_HEAD_CHARS]}\n\n…(長いため途中の {omitted:,} 文字を省きました)…\n\n{text[-tail:]}"


async def load(files: list[dict[str, Any]], fetch: Fetcher) -> tuple[list[Attachment], list[Skipped]]:
    loaded: list[Attachment] = []
    skipped: list[Skipped] = []
    for f in files:
        name = f.get("name") or f.get("title") or "(名前なし)"
        if f.get("mode") in ("tombstone", "external") or f.get("file_access") == "file_not_found":
            skipped.append(Skipped(name, "読み取れない種類のファイル"))
            continue
        kind = kind_of(f)
        if kind is None:
            skipped.append(Skipped(name, "画像とテキスト以外の形式"))
            continue
        if len(loaded) >= MAX_FILES:
            skipped.append(Skipped(name, f"1回に読めるのは {MAX_FILES} 個まで"))
            continue
        limit = MAX_IMAGE_BYTES if kind == "image" else MAX_TEXT_BYTES
        if int(f.get("size") or 0) > limit:
            skipped.append(Skipped(name, f"{limit // (1024 * 1024)}MB を超えている"))
            continue
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            skipped.append(Skipped(name, "ダウンロード先が分からない"))
            continue
        try:
            data = await fetch(url)
        except Exception:
            log.warning("添付 %s を取得できませんでした", name, exc_info=True)
            skipped.append(Skipped(name, "取得に失敗した"))
            continue
        if kind == "image":
            loaded.append(Attachment(name, "image", f["mimetype"].lower(), data=data))
        else:
            loaded.append(Attachment(name, "text", "text/plain", text=shorten(decode_text(data))))
    return loaded, skipped


def slack_fetcher(token: str) -> Fetcher:
    async def fetch(url: str) -> bytes:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                resp.raise_for_status()
                # 権限が足りないと、ファイルの代わりにログイン画面の HTML が返る
                if resp.content_type == "text/html":
                    raise PermissionError("ファイルを読む権限(files:read)がない可能性があります")
                return await resp.read()

    return fetch
