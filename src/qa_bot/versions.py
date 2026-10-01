"""作業ツリーを持たないミラー(bare リポジトリ)から、バージョンを指定して中身を読む

エージェントにはファイルシステムを直接触らせず、このモジュールの関数だけを渡す。
git は決まった読み取り専用のサブコマンドだけを、シェルを通さず引数の配列で実行する。
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

OFFICIAL_RE = re.compile(r"^v\d+(\.\d+){1,3}$")
BETA_RE = re.compile(r"^v\d+\.\d+\.\d+-beta\d+$")
DEVELOP = "develop"

MAX_FILE_BYTES = 512 * 1024
MAX_OUTPUT_CHARS = 60_000


class VersionError(ValueError):
    """エージェントに理由を返してやり直させる誤り"""


@dataclass(frozen=True)
class Version:
    name: str
    kind: str  # official / beta / develop

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.name}" if self.kind == "develop" else f"refs/tags/{self.name}"

    @property
    def label(self) -> str:
        return {"official": "正式リリース", "beta": "ベータ版", "develop": "開発中(未リリース)"}[self.kind]


def _version_key(name: str) -> tuple:
    m = re.match(r"^v(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-beta(\d+))?$", name)
    if not m:
        return (0, 0, 0, 0, 0, 0)
    nums = [int(x or 0) for x in m.group(1, 2, 3, 4)]
    beta = m.group(5)
    # 同じ番号ならベータを正式版より前に並べる
    return (*nums, 0 if beta else 1, int(beta or 0))


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(長すぎるため {len(text) - limit:,} 文字を省略。範囲や条件を絞ってください)"


class VersionRepo:
    def __init__(self, git_dir: Path, deny_patterns: tuple[str, ...] = (), git: str = "git", timeout: float = 30):
        self.git_dir = git_dir
        self.deny_patterns = deny_patterns
        self.git = git
        self.timeout = timeout

    # ----- git の実行 -----

    def _run(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
        cmd = [self.git, "--no-pager", f"--git-dir={self.git_dir}", "-c", "core.quotepath=off", *args]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=self.timeout,
            # 利用者の git 設定(エイリアスや外部 diff など)を読まない
            env=_git_env(),
        )
        if proc.returncode not in ok_codes:
            raise VersionError(proc.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} に失敗しました")
        return proc.stdout.decode("utf-8", "replace")

    # ----- バージョン -----

    def versions(self) -> list[Version]:
        out = self._run("for-each-ref", "--format=%(refname)", "refs/tags", "refs/heads")
        found: list[Version] = []
        for ref in out.split():
            if ref.startswith("refs/tags/"):
                name = ref[len("refs/tags/"):]
                if OFFICIAL_RE.match(name):
                    found.append(Version(name, "official"))
                elif BETA_RE.match(name):
                    found.append(Version(name, "beta"))
            elif ref == f"refs/heads/{DEVELOP}":
                found.append(Version(DEVELOP, "develop"))
        tags = sorted((v for v in found if v.kind != "develop"), key=lambda v: _version_key(v.name))
        return tags + [v for v in found if v.kind == "develop"]

    def latest_official(self) -> Version:
        officials = [v for v in self.versions() if v.kind == "official"]
        if not officials:
            raise VersionError("正式リリースのタグが見つかりません")
        return officials[-1]

    def resolve(self, name: str | None) -> Version:
        name = (name or "").strip()
        if not name or name in ("latest", "最新"):
            return self.latest_official()
        if not name.startswith("v") and name != DEVELOP:
            name = "v" + name
        for v in self.versions():
            if v.name == name:
                return v
        raise VersionError(f"{name} は対象のバージョンにありません。list_versions で一覧を確認してください")

    # ----- パス -----

    def _check_path(self, path: str, allow_empty: bool = True) -> str:
        path = (path or "").strip().replace("\\", "/")
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
            raise VersionError("リポジトリ内の相対パスを指定してください")
        path = path.strip("/")
        if not path:
            if allow_empty:
                return ""
            raise VersionError("path を指定してください")
        p = PurePosixPath(path)
        if path.startswith((":", "-", "~")) or ".." in p.parts or any(c in path for c in "\0\n"):
            raise VersionError("リポジトリ内の相対パスを指定してください")
        if self._denied(path):
            raise VersionError(f"{path} は参照が禁止されています")
        return path

    def _denied(self, path: str) -> bool:
        for pattern in self.deny_patterns:
            if pattern.endswith("/**"):
                base = pattern[:-3]
                if path == base or path.startswith(base + "/"):
                    return True
            elif fnmatch.fnmatchcase(path, pattern):
                return True
        return False

    def _pathspec(self, path: str, glob: str | None = None) -> list[str]:
        specs: list[str] = []
        if glob:
            if glob.startswith(("/", ":", "~")) or ".." in PurePosixPath(glob).parts:
                raise VersionError("glob にはリポジトリ内のパターンを指定してください")
            base = f"{path}/" if path else ""
            # 「*.cs」のようにディレクトリを含まないパターンは、どの階層にも当てはめる
            pattern = glob if "/" in glob else f"**/{glob}"
            specs.append(f":(glob){base}{pattern}")
        elif path:
            specs.append(f":(literal){path}")
        for pattern in self.deny_patterns:
            specs.append(f":(exclude,glob){pattern}")
        return specs

    # ----- エージェントに渡す操作 -----

    def list_versions_text(self) -> str:
        vs = self.versions()
        latest = self.latest_official().name if any(v.kind == "official" for v in vs) else "-"
        lines = [f"最新の正式リリース: {latest}", "", "バージョン一覧(古い順):"]
        lines += [f"- {v.name}({v.label})" for v in vs]
        return "\n".join(lines)

    def list_files(self, version: str | None, path: str = "", glob: str | None = None, limit: int = 500) -> str:
        v = self.resolve(version)
        path = self._check_path(path)
        if glob and (glob.startswith(("/", ":", "~")) or ".." in PurePosixPath(glob).parts):
            raise VersionError("glob にはリポジトリ内のパターンを指定してください")
        out = self._run("ls-tree", "-r", "--name-only", v.ref)
        files = []
        for f in out.splitlines():
            if not f or self._denied(f):
                continue
            if path and not (f == path or f.startswith(path + "/")):
                continue
            if glob and not fnmatch.fnmatchcase(f[len(path) + 1:] if path else f, glob) \
                    and not fnmatch.fnmatchcase(PurePosixPath(f).name, glob):
                continue
            files.append(f)
        if not files:
            return f"[{v.name}] 該当するファイルはありません"
        head = files[:limit]
        more = f"\n…ほか {len(files) - limit:,} 件(path や glob で絞ってください)" if len(files) > limit else ""
        return f"[{v.name}] {len(files):,} 件\n" + "\n".join(head) + more

    def read_file(self, version: str | None, path: str, offset: int = 1, limit: int = 400) -> str:
        v = self.resolve(version)
        path = self._check_path(path, allow_empty=False)
        size_out = self._run("cat-file", "-s", f"{v.ref}:{path}", ok_codes=(0, 128))
        if not size_out.strip().isdigit():
            raise VersionError(f"{v.name} に {path} はありません")
        if int(size_out) > MAX_FILE_BYTES:
            raise VersionError(f"{path} は大きすぎるため読めません({int(size_out):,} バイト)。grep で必要な箇所を探してください")
        data = self._run("cat-file", "blob", f"{v.ref}:{path}")
        if "\0" in data[:8000]:
            return f"[{v.name}] {path} はバイナリファイルのため表示できません"
        lines = data.lstrip("﻿").splitlines()
        if not lines:
            return f"[{v.name}] {path} は空のファイルです"
        start = max(1, int(offset))
        end = min(len(lines), start + max(1, int(limit)) - 1)
        body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
        rest = f"\n…(全 {len(lines):,} 行。続きは offset={end + 1} で読めます)" if end < len(lines) else ""
        return _clip(f"[{v.name}] {path}({start}〜{end} 行目)\n{body}{rest}")

    def grep(self, version: str | None, pattern: str, path: str = "", glob: str | None = None,
             ignore_case: bool = False, limit: int = 200) -> str:
        v = self.resolve(version)
        path = self._check_path(path)
        if not pattern:
            raise VersionError("pattern を指定してください")
        args = ["grep", "-n", "-I", "-E", "--full-name"]
        if ignore_case:
            args.append("-i")
        out = self._run(*args, "-e", pattern, v.ref, "--", *self._pathspec(path, glob), ok_codes=(0, 1))
        prefix = f"{v.ref}:"
        hits = [line[len(prefix):] if line.startswith(prefix) else line for line in out.splitlines()]
        hits = [h for h in hits if not self._denied(h.split(":", 1)[0])]
        if not hits:
            return f"[{v.name}] 一致する箇所はありません"
        more = f"\n…ほか {len(hits) - limit:,} 件(path や glob で絞ってください)" if len(hits) > limit else ""
        return _clip(f"[{v.name}] {len(hits):,} 件\n" + "\n".join(hits[:limit]) + more)

    def diff_summary(self, from_version: str, to_version: str, path: str = "") -> str:
        a, b = self.resolve(from_version), self.resolve(to_version)
        path = self._check_path(path)
        out = self._run("diff", "--name-status", "-M", a.ref, b.ref, "--", *self._pathspec(path))
        rows = [r for r in out.splitlines() if r and not self._denied(r.split("\t")[-1])]
        if not rows:
            return f"{a.name} → {b.name}: 変更はありません"
        return _clip(f"{a.name} → {b.name}: {len(rows):,} ファイル(A=追加 M=変更 D=削除 R=名前変更)\n" + "\n".join(rows))

    def diff_file(self, from_version: str, to_version: str, path: str) -> str:
        a, b = self.resolve(from_version), self.resolve(to_version)
        path = self._check_path(path, allow_empty=False)
        out = self._run("diff", "-M", "--no-color", "--no-ext-diff", a.ref, b.ref, "--", f":(literal){path}")
        return _clip(f"{a.name} → {b.name}: {path}\n" + (out or "変更はありません"))

    def log(self, from_version: str, to_version: str, path: str = "", limit: int = 200) -> str:
        a, b = self.resolve(from_version), self.resolve(to_version)
        path = self._check_path(path)
        out = self._run("log", "--no-merges", f"--max-count={int(limit)}", "--format=%h %ad %s", "--date=short",
                        f"{a.ref}..{b.ref}", "--", *self._pathspec(path))
        return _clip(f"{a.name} → {b.name} のコミット(新しい順)\n" + (out or "該当するコミットはありません"))


def _git_env() -> dict[str, str]:
    """利用者の git 設定(エイリアスや外部 diff など)を読まないようにした環境変数"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return env
