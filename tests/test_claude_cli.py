import sys

import pytest

from qa_bot.claude_cli import VERIFIED_CLI_VERSION, find_cli, older_than


@pytest.mark.parametrize("a, b, expected", [
    ("2.1.274", "2.1.286", True), ("2.1.286", "2.1.286", False), ("2.2.0", "2.1.286", False), ("2.1.9", "2.1.10", True),
])
def test_版を数値として比べる(a, b, expected):
    assert older_than(a, b) is expected


@pytest.mark.skipif(sys.platform == "win32", reason="シェルスクリプトで偽の CLI を作るため")
def test_設定した古いCLIの版を読み取る(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '2.1.274 (Claude Code)'\n")
    fake.chmod(0o755)
    info = find_cli(str(fake))
    assert info.source.startswith("設定") and info.version == "2.1.274"
    assert older_than(info.version, VERIFIED_CLI_VERSION)
