"""環境変数から設定を読み込み、認証モードと利用者の組み合わせを検証する"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping
from zoneinfo import ZoneInfo

AuthMode = Literal["subscription", "api"]

# CLI から質問したときの利用者 ID。CLI はボットを動かしている本人しか使えない前提
CLI_USER_ID = "cli"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """起動を止めるべき設定の誤り"""


@dataclass(frozen=True)
class Settings:
    auth_mode: AuthMode
    mirror_path: Path
    model: str
    effort: str | None
    max_turns: int
    max_budget_usd: float | None
    max_concurrency: int
    daily_limit_per_user: int
    timezone: ZoneInfo
    data_dir: Path
    pricing_path: Path
    system_prompt_path: Path
    faq_path: Path
    deny_paths: tuple[str, ...]
    allowed_slack_users: frozenset[str] | None  # None は全員を許可
    admin_slack_users: frozenset[str]
    retention_days: int = 365
    session_retention_days: int = 30
    git_path: str = "git"
    slack_bot_token: str | None = field(default=None, repr=False)
    slack_app_token: str | None = field(default=None, repr=False)
    show_cost_footer: bool = True
    log_question_text: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "qa_bot.sqlite3"

    @property
    def agent_workdir(self) -> Path:
        """エージェントの作業ディレクトリ。中身は空で、会話のセッション記録の置き場所を分けるためだけに使う"""
        return self.data_dir / "agent_workdir"

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token and self.slack_app_token)

    def is_slack_user_allowed(self, user_id: str) -> bool:
        if self.allowed_slack_users is None:
            return True
        return user_id in self.allowed_slack_users

    def is_admin(self, user_id: str) -> bool:
        return user_id == CLI_USER_ID or user_id in self.admin_slack_users


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _path(value: str | None, default: str) -> Path:
    p = Path(value or default).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise ConfigError(f"{key} には整数を指定してください") from None


def load_dotenv(path: Path) -> dict[str, str]:
    """`.env` を読む。値の展開などはせず KEY=VALUE の形だけを扱う"""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        # 実際の環境変数を .env より優先する
        env = {**load_dotenv(PROJECT_ROOT / ".env"), **os.environ}

    auth_mode = env.get("AUTH_MODE", "").strip()
    if auth_mode not in ("subscription", "api"):
        raise ConfigError("AUTH_MODE には subscription か api を指定してください")

    mirror = _path(env.get("MIRROR_PATH"), "data/mirror.git").resolve()
    if not (mirror / "HEAD").is_file() or not (mirror / "objects").is_dir():
        raise ConfigError(
            f"MIRROR_PATH に Git のミラー(bare リポジトリ)がありません: {mirror}\n"
            "scripts/sync_repo.ps1(Windows)か scripts/sync_repo.sh で作成してください"
        )

    allowed_raw = _split(env.get("ALLOWED_SLACK_USERS"))
    allowed: frozenset[str] | None
    if allowed_raw == ["*"]:
        allowed = None
    elif "*" in allowed_raw:
        raise ConfigError("ALLOWED_SLACK_USERS の * は単独で指定してください")
    else:
        allowed = frozenset(allowed_raw)

    has_api_key = bool(env.get("ANTHROPIC_API_KEY", "").strip())

    if auth_mode == "subscription":
        # 個人の使用枠をほかの人に使わせると規約違反になるため、本人1人に限る
        if allowed is None or len(allowed) > 1:
            raise ConfigError(
                "AUTH_MODE=subscription では ALLOWED_SLACK_USERS に自分の Slack ユーザー ID を"
                "1つだけ指定してください(空欄なら Slack からは誰も使えません)。"
                "ほかの人に公開するときは AUTH_MODE=api に切り替えてください"
            )
        if has_api_key:
            raise ConfigError(
                "AUTH_MODE=subscription なのに ANTHROPIC_API_KEY が設定されています。"
                "API キーが優先されて従量課金になるため、どちらかに揃えてください"
            )
    elif not has_api_key:
        raise ConfigError("AUTH_MODE=api では ANTHROPIC_API_KEY を設定してください")

    budget_raw = env.get("MAX_BUDGET_USD", "2.0").strip()
    try:
        timezone = ZoneInfo(env.get("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo")
    except Exception:
        raise ConfigError("TIMEZONE には Asia/Tokyo のようなタイムゾーン名を指定してください") from None

    return Settings(
        auth_mode=auth_mode,  # type: ignore[arg-type]
        mirror_path=mirror,
        model=env.get("MODEL", "claude-sonnet-5-5").strip(),
        effort=env.get("EFFORT", "").strip() or None,
        max_turns=_int(env, "MAX_TURNS", 30),
        max_budget_usd=float(budget_raw) if budget_raw else None,
        max_concurrency=max(1, _int(env, "MAX_CONCURRENCY", 2)),
        daily_limit_per_user=_int(env, "DAILY_LIMIT_PER_USER", 20),
        timezone=timezone,
        data_dir=_path(env.get("DATA_DIR"), "data"),
        pricing_path=_path(env.get("PRICING_PATH"), "config/pricing.toml"),
        system_prompt_path=_path(env.get("SYSTEM_PROMPT_PATH"), "config/system_prompt.md"),
        faq_path=_path(env.get("FAQ_PATH"), "knowledge/faq.md"),
        deny_paths=tuple(_split(env.get("DENY_PATHS", ".git/**"))),
        allowed_slack_users=allowed,
        admin_slack_users=frozenset(_split(env.get("ADMIN_SLACK_USERS"))),
        retention_days=_int(env, "RETENTION_DAYS", 365),
        session_retention_days=_int(env, "SESSION_RETENTION_DAYS", 30),
        git_path=env.get("GIT_PATH", "git").strip() or "git",
        slack_bot_token=env.get("SLACK_BOT_TOKEN", "").strip() or None,
        slack_app_token=env.get("SLACK_APP_TOKEN", "").strip() or None,
        show_cost_footer=_bool(env.get("SHOW_COST_FOOTER"), True),
        log_question_text=_bool(env.get("LOG_QUESTION_TEXT"), True),
    )
