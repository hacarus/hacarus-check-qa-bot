"""コマンドラインの入口。Slack がなくても質問・集計・設定確認ができる"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .agent import ClaudeAgentRunner, FakeAgentRunner, delete_session
from .claude_cli import VERIFIED_CLI_VERSION, find_cli, older_than
from .config import CLI_USER_ID, ConfigError, Settings, load_settings
from .guard import is_allowed
from .pricing import PriceTable
from .report import export_csv, format_summary, summarize
from .service import DailyLimitError, QAService
from .store import Store
from .versions import VersionError, VersionRepo


def _repo(settings: Settings) -> VersionRepo:
    return VersionRepo(settings.mirror_path, settings.deny_paths, git=settings.git_path)


def _build(settings: Settings, fake: bool) -> QAService:
    prices = PriceTable.load(settings.pricing_path)
    runner = FakeAgentRunner(settings.model) if fake else ClaudeAgentRunner(settings, _repo(settings))
    return QAService(settings, runner, Store(settings.db_path), prices,
                     session_deleter=lambda sid: delete_session(settings, sid))


def _print_answer(result) -> None:
    a = result.answer
    print(a.text)
    cost = "不明" if result.virtual_cost_usd is None else f"${result.virtual_cost_usd:.4f}"
    tokens = ", ".join(
        f"{m}: 入力{u.input:,}/出力{u.output:,}/書込{u.cache_write:,}/読出{u.cache_read:,}"
        for m, u in a.model_usage.items()
    )
    print(
        f"\n--- {a.num_turns} 往復 / {a.duration_ms / 1000:.1f} 秒 / 仮想料金 {cost}"
        f" / 認証 {a.auth_source or '不明'} / Claude Code {a.cli_version or '不明'}"
        f" / 拒否したツール呼び出し {a.permission_denials} 件"
        f"{' / 会話の続き' if result.resumed else ''}\n--- {tokens}",
        file=sys.stderr,
    )


def cmd_check(settings: Settings, _args) -> int:
    repo = _repo(settings)
    print(f"認証モード      : {settings.auth_mode}")
    print(f"ミラー          : {settings.mirror_path}")
    print(f"モデル          : {settings.model}(effort: {settings.effort or '既定'})")
    print(f"1問の上限       : {settings.max_turns} 往復 / ${settings.max_budget_usd}")
    print(f"1人1日の上限    : {settings.daily_limit_per_user or 'なし'} 件")
    users = "全員" if settings.allowed_slack_users is None else (", ".join(sorted(settings.allowed_slack_users)) or "なし(CLI のみ)")
    print(f"Slack の利用者  : {users}")
    print(f"Slack 連携      : {'有効' if settings.slack_enabled else '未設定'}")
    print(f"記録先          : {settings.db_path}(保存 {settings.retention_days} 日、セッション {settings.session_retention_days} 日)")

    ok = True
    try:
        vs = repo.versions()
        print(f"バージョン      : {len(vs)} 個(最新の正式リリース {repo.latest_official().name})")
    except VersionError as e:
        print(f"バージョン      : 取得できません({e})", file=sys.stderr)
        ok = False

    cli = find_cli(settings.claude_cli_path)
    print(f"Claude Code     : {cli.version or '不明'}({cli.source}{': ' + cli.path if cli.path else ''})")
    if cli.path is None:
        print("  NG: Claude Code が見つかりません。Claude Code をインストールするか CLAUDE_CLI_PATH を設定してください")
        ok = False
    elif cli.version and older_than(cli.version, VERIFIED_CLI_VERSION):
        print(f"  注意: {VERIFIED_CLI_VERSION} より古い版です。{settings.model} を知らない可能性があり、"
              "その場合はモデルに合わせた設定が使われません。`claude update` で更新してください")

    prices = PriceTable.load(settings.pricing_path)
    if prices.find(settings.model) is None:
        print(f"警告: {settings.model} の単価が {settings.pricing_path} にありません", file=sys.stderr)

    # 読み取り制限が効いているかを、実際に Claude を呼ばずに確かめる
    print("\nツール制限の確認")
    for tool, expected in [("mcp__repo__read_file", True), ("mcp__repo__grep", True),
                           ("Read", False), ("Bash", False), ("Edit", False), ("WebFetch", False)]:
        mark = "OK" if is_allowed(tool) == expected else "NG"
        ok &= is_allowed(tool) == expected
        print(f"  [{mark}] {tool} → {'許可' if is_allowed(tool) else '拒否'}")
    for path in ["../secret.txt", "/etc/passwd", "C:/Windows/win.ini", ".git/config", ":(top)x"]:
        try:
            repo.read_file(None, path)
            print(f"  [NG] read_file {path} → 読めてしまいました")
            ok = False
        except VersionError:
            print(f"  [OK] read_file {path} → 拒否")
    return 0 if ok else 1


def cmd_versions(settings: Settings, _args) -> int:
    print(_repo(settings).list_versions_text())
    return 0


def cmd_purge(settings: Settings, _args) -> int:
    n = _build(settings, fake=True).purge()
    print(f"保存期間を過ぎた記録を消しました(会話セッション {n} 件)")
    return 0


async def _ask(settings: Settings, args) -> int:
    service = _build(settings, args.fake)
    try:
        result = await service.ask(CLI_USER_ID, args.question, thread_key=args.thread)
    except DailyLimitError as e:
        print(f"今日の上限({e.limit} 件)に達しました", file=sys.stderr)
        return 1
    _print_answer(result)
    return 1 if result.answer.is_error else 0


async def _chat(settings: Settings, args) -> int:
    service = _build(settings, args.fake)
    thread = f"cli:{uuid.uuid4()}"
    print("質問を入力してください。/new で新しい会話、/cost で今月の集計、/quit で終了します。")
    while True:
        try:
            line = await asyncio.to_thread(input, "\n> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        line = line.strip()
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line == "/new":
            thread = f"cli:{uuid.uuid4()}"
            print("新しい会話を始めます。")
            continue
        if line == "/cost":
            print(format_summary(summarize(service.store, service.prices, _this_month())))
            continue
        _print_answer(await service.ask(CLI_USER_ID, line, thread_key=thread))


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def cmd_cost(settings: Settings, args) -> int:
    store = Store(settings.db_path)
    prices = PriceTable.load(settings.pricing_path)
    month = None if args.all else (args.month or _this_month())
    projections = [int(x) for x in args.project.split(",") if x.strip()]
    print(format_summary(summarize(store, prices, month), projections))
    return 0


def cmd_export(settings: Settings, args) -> int:
    store = Store(settings.db_path)
    text = export_csv(store, None if args.all else (args.month or _this_month()))
    if args.output:
        # Excel で文字化けしないよう BOM 付きで書く
        Path(args.output).write_text(text, encoding="utf-8-sig")
        print(f"{args.output} に書き出しました")
    else:
        sys.stdout.write(text)
    return 0


async def _slack(settings: Settings, args) -> int:
    if not settings.slack_enabled:
        print("SLACK_BOT_TOKEN と SLACK_APP_TOKEN を設定してください", file=sys.stderr)
        return 2
    from .slack_app import run_socket_mode

    service = _build(settings, args.fake)
    await run_socket_mode(settings, service, service.prices)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qa-bot", description="HACARUS Check 2025 リポジトリ相談ボット")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="設定とツール制限を確認する(Claude は呼ばない)")
    sub.add_parser("versions", help="質問に使えるバージョンの一覧を表示する")
    sub.add_parser("purge", help="保存期間を過ぎた記録と会話セッションを消す")

    p = sub.add_parser("ask", help="1つ質問する")
    p.add_argument("question")
    p.add_argument("--thread", help="同じ値を渡すと前の質問の続きとして答える")
    p.add_argument("--fake", action="store_true", help="Claude を呼ばずにダミーの回答を返す")

    p = sub.add_parser("chat", help="対話形式で質問する")
    p.add_argument("--fake", action="store_true")

    for name, help_ in (("cost", "仮想料金を集計する"), ("export-csv", "記録を CSV に書き出す")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--month", help="YYYY-MM(UTC)。省略時は今月")
        p.add_argument("--all", action="store_true", help="全期間")
        if name == "cost":
            p.add_argument("--project", default="300,600,1500", help="見込みを出す月の質問数(カンマ区切り)")
        else:
            p.add_argument("-o", "--output")

    p = sub.add_parser("slack", help="Slack ボットとして動かす(Socket Mode)")
    p.add_argument("--fake", action="store_true")

    args = parser.parse_args(argv)
    level = logging.INFO if args.cmd == "slack" else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2

    if args.cmd == "check":
        return cmd_check(settings, args)
    if args.cmd == "versions":
        return cmd_versions(settings, args)
    if args.cmd == "purge":
        return cmd_purge(settings, args)
    if args.cmd == "ask":
        return asyncio.run(_ask(settings, args))
    if args.cmd == "chat":
        return asyncio.run(_chat(settings, args))
    if args.cmd == "cost":
        return cmd_cost(settings, args)
    if args.cmd == "export-csv":
        return cmd_export(settings, args)
    if args.cmd == "slack":
        return asyncio.run(_slack(settings, args))
    return 2


if __name__ == "__main__":
    sys.exit(main())
