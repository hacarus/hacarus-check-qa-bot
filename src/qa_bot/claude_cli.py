"""ボットが実際に使う Claude Code(CLI)の場所と版を調べる

Agent SDK は同梱の CLI を優先して使い、同梱されていなければ PATH 上の claude を使う。
Windows 向けの SDK は同梱が新しい版に追いつかないことがあり、その場合は手元に入れた
Claude Code が使われる。古い CLI は新しいモデルの情報を持たないため、check で確かめる。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# claude-sonnet-5-5 をモデル一覧に持つことを確かめた版
VERIFIED_CLI_VERSION = "2.1.286"


@dataclass(frozen=True)
class CliInfo:
    path: str | None
    source: str  # 設定 / 同梱 / PATH / 見つからない
    version: str | None


def _bundled_path() -> Path:
    import claude_agent_sdk

    name = "claude.exe" if platform.system() == "Windows" else "claude"
    return Path(claude_agent_sdk.__file__).parent / "_bundled" / name


def find_cli(configured: str | None = None) -> CliInfo:
    if configured:
        path, source = configured, "設定(CLAUDE_CLI_PATH)"
    elif _bundled_path().is_file():
        path, source = str(_bundled_path()), "SDK に同梱"
    else:
        found = shutil.which("claude.exe" if platform.system() == "Windows" else "claude") or shutil.which("claude")
        if not found and platform.system() == "Windows":
            candidate = Path.home() / ".local" / "bin" / "claude.exe"
            found = str(candidate) if candidate.is_file() else None
        if not found:
            return CliInfo(None, "見つからない", None)
        path, source = found, "PATH 上の Claude Code"
    return CliInfo(path, source, cli_version(path))


def cli_version(path: str) -> str | None:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "CLAUDECODE": ""}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"\d+\.\d+\.\d+", out)
    return m.group(0) if m else None


def older_than(version: str, other: str) -> bool:
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    return key(version) < key(other)
