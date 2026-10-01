import pytest

from qa_bot.versions import VersionError, VersionRepo


@pytest.fixture
def repo(mirror):
    return VersionRepo(mirror, (".git/**", "secrets/**"))


def test_正式リリースとベータとdevelopだけを古い順に並べる(repo):
    assert [v.name for v in repo.versions()] == ["v1.0.0", "v1.1.0", "v2.0.0-beta1", "develop"]
    assert repo.latest_official().name == "v1.1.0"


@pytest.mark.parametrize("given, expected", [
    (None, "v1.1.0"), ("", "v1.1.0"), ("latest", "v1.1.0"), ("1.0.0", "v1.0.0"), ("v2.0.0-beta1", "v2.0.0-beta1"),
    ("develop", "develop"),
])
def test_バージョン名を解決する(repo, given, expected):
    assert repo.resolve(given).name == expected


@pytest.mark.parametrize("name", ["v1.1.0-rc1", "trace-tool-v0.0.1", "v9.9.9", "HEAD", "main"])
def test_対象外のバージョンは使えない(repo, name):
    with pytest.raises(VersionError):
        repo.resolve(name)


def test_バージョンごとにファイルを読める(repo):
    assert "MaxCount = 2" in repo.read_file("v1.0.0", "src/Camera.cs")
    assert "MaxCount = 4" in repo.read_file(None, "src/Camera.cs")
    assert "1\tpublic class Camera" in repo.read_file(None, "src/Camera.cs")


def test_長いファイルは範囲を指定して読む(repo):
    text = repo.read_file(None, "src/Camera.cs", offset=2, limit=1)
    assert "MaxCount" in text and "public class" not in text
    assert "offset=3" in text


def test_存在しないファイルとバイナリ(repo):
    with pytest.raises(VersionError):
        repo.read_file("v1.0.0", "src/Export.cs")
    assert "バイナリ" in repo.read_file(None, "image.bin")


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "C:/Windows/win.ini", ":(top)README.md", "-n",
                                  ".git/config", "secrets/key.txt"])
def test_リポジトリ外や禁止パスは読めない(repo, path):
    with pytest.raises(VersionError):
        repo.read_file(None, path)


def test_一覧と検索から禁止パスを除く(repo):
    files = repo.list_files(None)
    assert "src/Camera.cs" in files and "secrets/key.txt" not in files
    assert "一致する箇所はありません" in repo.grep(None, "SECRET")


def test_globで絞り込む(repo):
    assert "src/Camera.cs" in repo.list_files(None, glob="*.cs")
    assert "README.md" not in repo.list_files(None, glob="*.cs")
    assert "src/Camera.cs:2:" in repo.grep(None, "MaxCount", glob="*.cs")
    with pytest.raises(VersionError):
        repo.grep(None, "x", glob="../*")


def test_検索語がオプションに見えても検索語として扱う(repo):
    assert "一致する箇所はありません" in repo.grep(None, "--output=/tmp/x")


def test_バージョン間の差分と履歴(repo):
    summary = repo.diff_summary("v1.1.0", "v2.0.0-beta1")
    assert "M\tconfig/settings.json" in summary and "A\tsrc/Export.cs" in summary
    assert '"unit": "mm"' in repo.diff_file("v1.1.0", "v2.0.0-beta1", "config/settings.json")
    assert "設定の形式を v2 に変更" in repo.log("v1.1.0", "v2.0.0-beta1")
    assert "カメラを4台まで" not in repo.log("v1.1.0", "v2.0.0-beta1")


def test_バージョン一覧の文章(repo):
    text = repo.list_versions_text()
    assert "最新の正式リリース: v1.1.0" in text and "ベータ版" in text and "未リリース" in text
