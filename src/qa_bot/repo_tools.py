"""versions.py の操作を、Agent SDK のツール(同じプロセス内の MCP サーバー)として公開する"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .guard import SERVER_NAME
from .versions import VersionError, VersionRepo

VERSION_HELP = "バージョン名(例: v3.3.2、v4.0.0-beta1、develop)。省略すると最新の正式リリース"


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


def _wrap(fn: Callable[[dict[str, Any]], str]) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            # git の実行で待つ間もイベントループ(Slack の応答など)を止めない
            return _text(await asyncio.to_thread(fn, args))
        except VersionError as e:
            return _text(str(e), is_error=True)
        except Exception as e:  # git のタイムアウトなど
            return _text(f"ツールの実行に失敗しました: {e}", is_error=True)

    return handler


def tool_specs(repo: VersionRepo) -> list[tuple[str, str, dict[str, Any], Callable]]:
    version = {"type": "string", "description": VERSION_HELP}
    path = {"type": "string", "description": "リポジトリ内の相対パス(フォルダかファイル)。省略するとリポジトリ全体"}
    return [
        ("list_versions", "質問に使えるバージョンの一覧と、最新の正式リリースを返す",
         _schema({}, []), lambda a: repo.list_versions_text()),
        ("list_files", "指定したバージョンのファイル一覧を返す。glob(例: *.cs、docs/*.md)で絞れる",
         _schema({"version": version, "path": path, "glob": {"type": "string"}}, []),
         lambda a: repo.list_files(a.get("version"), a.get("path", ""), a.get("glob"))),
        ("read_file", "指定したバージョンのファイルを行番号付きで読む。長いファイルは offset と limit で分けて読む",
         _schema({"version": version, "path": {"type": "string", "description": "リポジトリ内のファイルの相対パス"},
                  "offset": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 2000}},
                 ["path"]),
         lambda a: repo.read_file(a.get("version"), a["path"], a.get("offset", 1), a.get("limit", 400))),
        ("grep", "指定したバージョンのファイルを正規表現(拡張正規表現)で検索し、パス:行番号:内容 を返す",
         _schema({"version": version, "pattern": {"type": "string"}, "path": path, "glob": {"type": "string"},
                  "ignore_case": {"type": "boolean"}}, ["pattern"]),
         lambda a: repo.grep(a.get("version"), a["pattern"], a.get("path", ""), a.get("glob"), bool(a.get("ignore_case")))),
        ("diff_summary", "2つのバージョンの間で変わったファイルの一覧を返す。マイグレーションの要否を調べる最初の手がかり",
         _schema({"from_version": version, "to_version": version, "path": path}, ["from_version", "to_version"]),
         lambda a: repo.diff_summary(a["from_version"], a["to_version"], a.get("path", ""))),
        ("diff_file", "2つのバージョンの間の、1つのファイルの差分を返す",
         _schema({"from_version": version, "to_version": version, "path": {"type": "string"}},
                 ["from_version", "to_version", "path"]),
         lambda a: repo.diff_file(a["from_version"], a["to_version"], a["path"])),
        ("log", "2つのバージョンの間のコミット(マージを除く)を新しい順に返す",
         _schema({"from_version": version, "to_version": version, "path": path}, ["from_version", "to_version"]),
         lambda a: repo.log(a["from_version"], a["to_version"], a.get("path", ""))),
    ]


def build_server(repo: VersionRepo):
    from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

    tools = [
        tool(name, desc, schema, annotations=ToolAnnotations(readOnlyHint=True, maxResultSizeChars=80_000))(_wrap(fn))
        for name, desc, schema, fn in tool_specs(repo)
    ]
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)
