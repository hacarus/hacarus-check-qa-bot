"""エージェントが呼べるツールを、Git を読む専用ツールだけに限る

Claude Code の組み込みツール(Read / Bash / Edit など)は起動時に1つも渡さないが、
念のため、すべてのツール呼び出しの直前に走る PreToolUse フックでも許可リストを確かめる。
パスやバージョンの検査は、専用ツールの中(versions.py)で行う。
"""

from __future__ import annotations

from typing import Any

SERVER_NAME = "repo"
TOOL_NAMES = ("list_versions", "list_files", "read_file", "grep", "diff_summary", "diff_file", "log")
ALLOWED_TOOLS = frozenset(f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES)

# 念のため明示的に禁止する組み込みツール
DISALLOWED_TOOLS = (
    "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Agent", "Task", "Skill",
)


def is_allowed(tool_name: str) -> bool:
    return tool_name in ALLOWED_TOOLS


def make_pre_tool_use_hook():
    """Agent SDK の PreToolUse フックとして渡すコールバックを作る"""

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        name = input_data.get("tool_name", "")
        if is_allowed(name):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{name} は使用できません(読み取り専用の相談窓口です)",
            }
        }

    return hook
