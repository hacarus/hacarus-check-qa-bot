"""エージェントのツール呼び出しを、対象リポジトリ内の読み取りだけに制限する

Claude Code のツール設定(tools / disallowed_tools)に加えて、すべてのツール呼び出しの
直前に走る PreToolUse フックでパスを検査する。Read ツールは絶対パスを渡せば
リポジトリの外(認証情報や /proc/self/environ など)も読めてしまうため、ここで塞ぐ。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

READ_ONLY_TOOLS = frozenset({"Read", "Grep", "Glob"})

# 念のため明示的に禁止するツール。READ_ONLY_TOOLS 以外はフックでも拒否する
DISALLOWED_TOOLS = (
    "Bash",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Agent",
    "Task",
)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


class PathGuard:
    def __init__(self, repo_path: Path, deny_patterns: tuple[str, ...] = (".git/**",)):
        self.repo = repo_path.resolve()
        self.deny_patterns = deny_patterns

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        if tool_name not in READ_ONLY_TOOLS:
            return Decision(False, f"{tool_name} は使用できません(読み取り専用の相談窓口です)")

        if tool_name == "Read":
            target = tool_input.get("file_path")
            if not target:
                return Decision(False, "file_path がありません")
        else:
            target = tool_input.get("path") or str(self.repo)

        decision = self._check_path(str(target))
        if not decision.allowed:
            return decision

        # Glob の pattern や Grep の glob に .. や絶対パスを混ぜて外へ出るのを防ぐ
        for key in ("pattern", "glob"):
            if tool_name == "Grep" and key == "pattern":
                continue  # Grep の pattern は検索する正規表現でありパスではない
            value = tool_input.get(key)
            if isinstance(value, str) and (value.startswith(("/", "~")) or ".." in PurePosixPath(value).parts):
                return Decision(False, f"{key} にリポジトリ外を指す指定は使えません")

        return Decision(True)

    def _check_path(self, raw: str) -> Decision:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self.repo / p
        # シンボリックリンクをたどった実体で判定する
        resolved = p.resolve()
        try:
            rel = resolved.relative_to(self.repo)
        except ValueError:
            return Decision(False, "リポジトリの外は参照できません")

        rel_posix = rel.as_posix()
        for pattern in self.deny_patterns:
            if _matches(rel_posix, pattern):
                return Decision(False, f"{rel_posix} は参照が禁止されています")
        return Decision(True)


def _matches(rel: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return rel == base or rel.startswith(base + "/")
    return fnmatch.fnmatchcase(rel, pattern)


def make_pre_tool_use_hook(guard: PathGuard):
    """Agent SDK の PreToolUse フックとして渡すコールバックを作る"""

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        decision = guard.check(input_data.get("tool_name", ""), input_data.get("tool_input") or {})
        if decision.allowed:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }
        }

    return hook
