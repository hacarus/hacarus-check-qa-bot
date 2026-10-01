from pathlib import Path

import pytest

from qa_bot.config import PROJECT_ROOT, load_settings
from qa_bot.pricing import PriceTable


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    (r / ".git" / "config").write_text("[core]\n")
    (r / "README.md").write_text("# test\n")
    (r / "secrets").mkdir()
    (r / "secrets" / "key.txt").write_text("xxx")
    return r


@pytest.fixture
def base_env(repo: Path, tmp_path: Path) -> dict[str, str]:
    return {
        "AUTH_MODE": "subscription",
        "REPO_PATH": str(repo),
        "DB_PATH": str(tmp_path / "qa.sqlite3"),
        "ALLOWED_SLACK_USERS": "UOWNER",
        "ADMIN_SLACK_USERS": "UOWNER",
    }


@pytest.fixture
def settings(base_env):
    return load_settings(base_env)


@pytest.fixture
def prices() -> PriceTable:
    return PriceTable.load(PROJECT_ROOT / "config" / "pricing.toml")
