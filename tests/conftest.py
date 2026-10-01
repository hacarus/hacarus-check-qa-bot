import os
import subprocess
from pathlib import Path

import pytest

from qa_bot.config import PROJECT_ROOT, load_settings
from qa_bot.pricing import PriceTable


def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _commit(work: Path, files: dict[str, str | bytes], message: str) -> None:
    for name, content in files.items():
        p = work / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", message)


@pytest.fixture(scope="session")
def mirror(tmp_path_factory) -> Path:
    """タグとブランチを持つ bare リポジトリ(本番のミラーと同じ形)"""
    base = tmp_path_factory.mktemp("repo")
    work = base / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "develop")

    _commit(work, {
        "README.md": "# サンプル\n",
        "src/Camera.cs": "public class Camera {\n    public int MaxCount = 2;\n}\n",
        "config/settings.json": '{"version": 1}\n',
        "secrets/key.txt": "SECRET\n",
        "image.bin": b"\x00\x01\x02binary",
    }, "最初の版")
    _git(work, "tag", "v1.0.0")

    _commit(work, {"src/Camera.cs": "public class Camera {\n    public int MaxCount = 4;\n}\n"}, "カメラを4台まで")
    _git(work, "tag", "v1.1.0-rc1")
    _git(work, "tag", "v1.1.0")
    _git(work, "tag", "trace-tool-v0.0.1")

    _commit(work, {"config/settings.json": '{"version": 2, "unit": "mm"}\n', "src/Export.cs": "class CsvExport {}\n"},
            "設定の形式を v2 に変更")
    _git(work, "tag", "v2.0.0-beta1")

    _commit(work, {"src/Next.cs": "class NextFeature {}\n"}, "開発中の機能")

    bare = base / "mirror.git"
    _git(base, "clone", "-q", "--bare", str(work), str(bare))
    return bare


@pytest.fixture
def base_env(mirror: Path, tmp_path: Path) -> dict[str, str]:
    return {
        "AUTH_MODE": "subscription",
        "MIRROR_PATH": str(mirror),
        "DATA_DIR": str(tmp_path / "data"),
        "ALLOWED_SLACK_USERS": "UOWNER",
        "ADMIN_SLACK_USERS": "UOWNER",
    }


@pytest.fixture
def settings(base_env):
    return load_settings(base_env)


@pytest.fixture
def prices() -> PriceTable:
    return PriceTable.load(PROJECT_ROOT / "config" / "pricing.toml")
