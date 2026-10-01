import os
from pathlib import Path

import pytest

from qa_bot.guard import PathGuard, make_pre_tool_use_hook


@pytest.fixture
def guard(repo):
    return PathGuard(repo, (".git/**", "secrets/**"))


def test_リポジトリ内は読める(guard, repo):
    assert guard.check("Read", {"file_path": str(repo / "README.md")}).allowed
    assert guard.check("Read", {"file_path": "README.md"}).allowed
    assert guard.check("Grep", {"pattern": "test"}).allowed
    assert guard.check("Glob", {"pattern": "**/*.md"}).allowed


@pytest.mark.parametrize("path", ["/etc/passwd", "/proc/self/environ", "~/.claude/.credentials.json", "../x", "/"])
def test_リポジトリ外は読めない(guard, path):
    assert not guard.check("Read", {"file_path": path}).allowed
    assert not guard.check("Grep", {"pattern": "x", "path": path}).allowed


def test_禁止パスは読めない(guard, repo):
    assert not guard.check("Read", {"file_path": str(repo / ".git" / "config")}).allowed
    assert not guard.check("Read", {"file_path": "secrets/key.txt"}).allowed
    assert not guard.check("Grep", {"pattern": "x", "path": "secrets"}).allowed


def test_シンボリックリンクで外に出られない(guard, repo, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, repo / "link.txt")
    assert not guard.check("Read", {"file_path": str(repo / "link.txt")}).allowed


@pytest.mark.parametrize("pattern", ["../**/*", "/etc/*", "~/*"])
def test_globパターンで外に出られない(guard, pattern):
    assert not guard.check("Glob", {"pattern": pattern}).allowed
    assert not guard.check("Grep", {"pattern": "x", "glob": pattern}).allowed


def test_Grepの正規表現にドットが含まれていても拒否しない(guard):
    assert guard.check("Grep", {"pattern": r"\.\./"}).allowed


@pytest.mark.parametrize("tool", ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "Agent", "mcp__github__push_files"])
def test_読み取り以外のツールは使えない(guard, tool, repo):
    assert not guard.check(tool, {"file_path": str(repo / "README.md"), "command": "ls"}).allowed


async def test_フックは拒否をSDKの形式で返す(guard):
    hook = make_pre_tool_use_hook(guard)
    out = await hook({"tool_name": "Bash", "tool_input": {"command": "git push"}}, "id", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await hook({"tool_name": "Read", "tool_input": {"file_path": "README.md"}}, "id", None) == {}
