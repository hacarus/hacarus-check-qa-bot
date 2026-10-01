"""Claude Agent SDK を、Git を読む専用ツールだけを持たせて呼び出す"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings
from .guard import ALLOWED_TOOLS, DISALLOWED_TOOLS, SERVER_NAME, make_pre_tool_use_hook
from .pricing import TokenUsage
from .repo_tools import build_server
from .versions import VersionRepo


@dataclass
class AgentAnswer:
    text: str
    session_id: str | None
    is_error: bool = False
    subtype: str = "success"
    num_turns: int = 0
    duration_ms: int = 0
    model_usage: dict[str, TokenUsage] = field(default_factory=dict)
    sdk_cost_usd: float | None = None
    auth_source: str | None = None
    permission_denials: int = 0


class AgentRunner(Protocol):
    async def ask(self, question: str, resume_session_id: str | None = None) -> AgentAnswer: ...


def build_system_prompt(settings: Settings) -> str:
    prompt = settings.system_prompt_path.read_text(encoding="utf-8")
    if settings.faq_path.exists():
        # 書き方の説明などの HTML コメントは除く
        faq = re.sub(r"<!--.*?-->", "", settings.faq_path.read_text(encoding="utf-8"), flags=re.S).strip()
        if faq:
            prompt += "\n\n## よくある質問(管理者がまとめたもの。ここで答えられる質問はリポジトリを調べなくてよい)\n\n" + faq
    return prompt


def build_options(settings: Settings, repo: VersionRepo, resume_session_id: str | None = None):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    settings.agent_workdir.mkdir(parents=True, exist_ok=True)
    env = {
        # エージェントの子プロセスに Slack のトークンを渡さない
        "SLACK_BOT_TOKEN": "",
        "SLACK_APP_TOKEN": "",
        "CLAUDE_AGENT_SDK_CLIENT_APP": "hacarus-check-qa-bot/0.2.0",
    }
    if settings.auth_mode == "api":
        # API キーが優先されるが、使用枠のトークンが紛れ込んでいても使われないよう空にする
        env["CLAUDE_CODE_OAUTH_TOKEN"] = ""

    kwargs: dict[str, Any] = dict(
        cwd=str(settings.agent_workdir),
        model=settings.model,
        system_prompt=build_system_prompt(settings),
        # 組み込みツールは1つも渡さず、Git を読む専用ツールだけを使わせる
        tools=[],
        mcp_servers={SERVER_NAME: build_server(repo)},
        allowed_tools=sorted(ALLOWED_TOOLS),
        disallowed_tools=list(DISALLOWED_TOOLS),
        permission_mode="dontAsk",
        # ~/.claude などの設定(hooks を含む)を読み込まない
        setting_sources=[],
        skills=[],
        strict_mcp_config=True,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_pre_tool_use_hook()])]},
        max_turns=settings.max_turns,
        env=env,
    )
    if settings.max_budget_usd is not None:
        kwargs["max_budget_usd"] = settings.max_budget_usd
    if settings.effort:
        kwargs["effort"] = settings.effort
    if resume_session_id:
        kwargs["resume"] = resume_session_id
    return ClaudeAgentOptions(**kwargs)


def parse_model_usage(model_usage: dict[str, Any] | None) -> dict[str, TokenUsage]:
    result: dict[str, TokenUsage] = {}
    for model, u in (model_usage or {}).items():
        result[model] = TokenUsage(
            input=int(u.get("inputTokens", 0) or 0),
            output=int(u.get("outputTokens", 0) or 0),
            cache_write=int(u.get("cacheCreationInputTokens", 0) or 0),
            cache_read=int(u.get("cacheReadInputTokens", 0) or 0),
        )
    return result


def parse_usage(usage: dict[str, Any] | None) -> TokenUsage:
    u = usage or {}
    return TokenUsage(
        input=int(u.get("input_tokens", 0) or 0),
        output=int(u.get("output_tokens", 0) or 0),
        cache_write=int(u.get("cache_creation_input_tokens", 0) or 0),
        cache_read=int(u.get("cache_read_input_tokens", 0) or 0),
    )


class ClaudeAgentRunner:
    def __init__(self, settings: Settings, repo: VersionRepo):
        self.settings = settings
        self.repo = repo

    async def ask(self, question: str, resume_session_id: str | None = None) -> AgentAnswer:
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, query

        options = build_options(self.settings, self.repo, resume_session_id)
        texts: list[str] = []
        auth_source: str | None = None
        result: ResultMessage | None = None

        async for message in query(prompt=question, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                auth_source = message.data.get("apiKeySource")
            elif isinstance(message, AssistantMessage):
                texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
            elif isinstance(message, ResultMessage):
                result = message

        if result is None:
            return AgentAnswer(text="回答を取得できませんでした", session_id=None, is_error=True, subtype="no_result")

        model_usage = parse_model_usage(result.model_usage)
        if not model_usage and result.usage:
            model_usage = {self.settings.model: parse_usage(result.usage)}

        text = result.result or (texts[-1] if texts else "")
        if result.is_error and not text:
            text = f"回答の途中で止まりました({result.subtype})"

        return AgentAnswer(
            text=text,
            session_id=result.session_id,
            is_error=result.is_error,
            subtype=result.subtype,
            num_turns=result.num_turns,
            duration_ms=result.duration_ms,
            model_usage=model_usage,
            sdk_cost_usd=result.total_cost_usd,
            auth_source=auth_source,
            permission_denials=len(result.permission_denials or []),
        )


class FakeAgentRunner:
    """Claude を呼ばずに定型の回答と架空のトークン数を返す。配線の確認やテストに使う"""

    def __init__(self, model: str = "claude-sonnet-5-5"):
        self.model = model
        self.calls: list[tuple[str, str | None]] = []

    async def ask(self, question: str, resume_session_id: str | None = None) -> AgentAnswer:
        self.calls.append((question, resume_session_id))
        n = len(self.calls)
        return AgentAnswer(
            text=f"(ダミー回答 {n}) 「{question}」を受け付けました。",
            session_id=resume_session_id or f"fake-session-{n}",
            num_turns=8,
            duration_ms=1234,
            model_usage={self.model: TokenUsage(input=1_000, output=8_000, cache_write=45_000, cache_read=255_000)},
            auth_source="fake",
        )


def delete_session(settings: Settings, session_id: str) -> None:
    """会話のセッション記録(Claude Code が残す JSONL)を消す"""
    from claude_agent_sdk import delete_session as sdk_delete_session

    try:
        sdk_delete_session(session_id, directory=str(settings.agent_workdir))
    except FileNotFoundError:
        pass
