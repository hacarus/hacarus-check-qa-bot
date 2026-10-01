import pytest

from qa_bot.agent import build_options
from qa_bot.guard import ALLOWED_TOOLS, is_allowed, make_pre_tool_use_hook
from qa_bot.repo_tools import _wrap, tool_specs
from qa_bot.versions import VersionError, VersionRepo


@pytest.mark.parametrize("tool", ["Read", "Bash", "Edit", "Write", "Glob", "Grep", "WebFetch", "Agent",
                                  "mcp__github__push_files", "mcp__repo__write_file"])
def test_読み取り専用ツール以外は使えない(tool):
    assert not is_allowed(tool)


def test_Gitを読む専用ツールは使える():
    assert {"mcp__repo__read_file", "mcp__repo__grep", "mcp__repo__diff_summary"} <= ALLOWED_TOOLS


async def test_フックは拒否をSDKの形式で返す():
    hook = make_pre_tool_use_hook()
    out = await hook({"tool_name": "Bash", "tool_input": {"command": "git push"}}, "id", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await hook({"tool_name": "mcp__repo__read_file", "tool_input": {"path": "README.md"}}, "id", None) == {}


def test_ツール定義と許可リストが一致する(mirror):
    names = {f"mcp__repo__{name}" for name, *_ in tool_specs(VersionRepo(mirror))}
    assert names == ALLOWED_TOOLS


async def test_ツールの誤りはエラーとしてエージェントに返す():
    def broken(_args):
        raise VersionError("v9.9.9 は対象のバージョンにありません")

    out = await _wrap(broken)({})
    assert out["is_error"] and "v9.9.9" in out["content"][0]["text"]


def test_組み込みツールを渡さず設定ファイルも読まない(settings, mirror):
    o = build_options(settings, VersionRepo(mirror), resume_session_id="abc")
    assert o.tools == []
    assert set(o.allowed_tools) == ALLOWED_TOOLS
    assert o.setting_sources == [] and o.permission_mode == "dontAsk"
    assert "Bash" in o.disallowed_tools and "Read" in o.disallowed_tools
    assert o.resume == "abc" and o.cwd == str(settings.agent_workdir)
    assert o.env["SLACK_BOT_TOKEN"] == ""
