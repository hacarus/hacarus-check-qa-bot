# hacarus-check-qa-bot

営業や技術サポートのメンバーが、HACARUS Check 2025 のリポジトリについて Slack で質問できるボットです。
回答には Claude Agent SDK(Claude Code と同じエージェント)を使い、リポジトリは読むことしかできないようにしています。
設計の背景や判断は設計書(HACARUS Check リポジトリ相談ボット 設計書)にまとめています。

## できること

- Slack の AI アプリのパネルで質問する。会話は一覧から選んで続けられ、最初の質問がタイトルになる
- チャンネルでボットにメンションしても答える
- 質問の文中でバージョンを指定できる(「v3.2.1 で…」「v3.2.1 から v3.3.2 に上げると…」)。指定がなければ最新の正式リリースで答える
- 回答ごとに 👍 / 👎 で評価でき、👎 のときは理由を任意で書ける
- 質問ごとのトークン数を記録し、API の従量課金だった場合の料金(仮想料金)を集計する

## 読み取り専用にする仕組み

- 質問対象のリポジトリは、作業ツリーを持たないミラー(bare リポジトリ)として置く
- エージェントには Claude Code の組み込みツール(Read、Bash、Edit など)を1つも渡さない。渡すのは、バージョンを指定して Git の中身を読む7つの専用ツールだけ(`src/qa_bot/versions.py`)
  - 専用ツールは、許可したバージョンとリポジトリ内の相対パスしか受け付けない
  - git は決まった読み取り用のサブコマンドだけを、シェルを通さずに実行する
- すべてのツール呼び出しの直前に、許可リストにないツールを拒否するフックが走る(`src/qa_bot/guard.py`)
- `~/.claude` などの Claude Code の設定(hooks を含む)は読み込まない
- 公開フェーズでは、書き込み権限のない Deploy key でミラーを同期し、ボットは専用の Windows ユーザーで動かす

質問に使うバージョンは、正式リリース(`v3.3.2`、`v3.2.1.4` など)、ベータ(`v4.0.0-beta1` など)、`develop` です。
`-rc` や `-test` が付くタグと、別製品のタグ(`vixio-`、`trace-tool-`)は使いません。

## 検証フェーズ(自分の PC で、自分の使用枠で、自分だけが使う)

Windows の PowerShell で実行します。Python 3.11 以上と Git for Windows が必要です。

```powershell
git clone https://github.com/koyo-matsuda-hacarus/hacarus-check-qa-bot
cd hacarus-check-qa-bot
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 質問対象のミラーを作る(自分の PC の git の認証で取得する。2回目以降は差分だけ取得する)
powershell -ExecutionPolicy Bypass -File scripts\sync_repo.ps1 -RepoUrl https://github.com/hacarus/hacarus-check-2025

copy .env.example .env           # AUTH_MODE=subscription のままにする
.venv\Scripts\qa-bot check       # 設定とツール制限を確認する(Claude は呼ばない)
.venv\Scripts\qa-bot versions    # 質問に使えるバージョンの一覧
.venv\Scripts\qa-bot ask "v3.2.1 から v3.3.2 に上げるとき、設定の移行は必要ですか？"
.venv\Scripts\qa-bot chat        # 対話形式で質問する(/new で新しい会話、/cost で集計)
```

- 手元で Claude Code にログイン済みなら、そのアカウントの使用枠で動きます
- `AUTH_MODE=subscription` のときは、Slack の利用者に自分以外を指定すると起動を拒否します。個人の使用枠をほかの人に使わせると規約違反になるためです
- `--fake` を付けると、Claude を呼ばずにダミーの回答を返します。記録や集計の流れだけを確かめるときに使います

### Slack の開発用サンドボックスで試す

1. [Slack Developer Program](https://api.slack.com/developer-program) に参加し、サンドボックスのワークスペースを作る
2. https://api.slack.com/apps で「Create New App」→「From an app manifest」を選び、サンドボックスを選んで `slack/manifest.yml` を貼り付ける
3. 「Install to Workspace」で Bot User OAuth Token(`xoxb-`)を、「Basic Information」→「App-Level Tokens」で `connections:write` のトークン(`xapp-`)を発行する
4. `.env` の `SLACK_BOT_TOKEN`、`SLACK_APP_TOKEN` と、`ALLOWED_SLACK_USERS` と `ADMIN_SLACK_USERS` に自分の Slack ユーザー ID を1つだけ書く
5. `.venv\Scripts\qa-bot slack` で起動し、Slack の右上の AI アプリのアイコンからボットを開いて質問する

### 仮想料金と評価の確認

```powershell
.venv\Scripts\qa-bot cost                        # 今月の件数・評価・1件あたりの平均と、月の件数ごとの見込み
.venv\Scripts\qa-bot cost --month 2026-10 --project 300,600,1500
.venv\Scripts\qa-bot export-csv -o usage.csv     # 稟議の資料用に CSV で書き出す(Excel で開ける)
```

Slack では `/qa-cost` で同じ集計を見られます(`ADMIN_SLACK_USERS` の人だけ)。
使用枠でも API でもトークンの数え方は同じなので、ここで測った数字がそのまま公開後の見積もりになります。
キャッシュ書き込みは 5 分 TTL の単価(入力の 1.25 倍)で計算しています。単価は `config/pricing.toml` にあります。

## 公開フェーズへの切り替え

コードは変えず、`.env` だけを差し替えます。

```
AUTH_MODE=api
ANTHROPIC_API_KEY=sk-ant-...     # Console でワークスペースの月の上限額も設定する
CLAUDE_CODE_OAUTH_TOKEN=         # 空にする
ALLOWED_SLACK_USERS=U...,U...    # 質問できる人の ID(* で全員)
```

`AUTH_MODE=api` なのに API キーがない場合や、`AUTH_MODE=subscription` なのに API キーが残っている場合も起動を拒否します。
どちらの認証で動いたかは質問ごとに記録しており、`export-csv` の `auth_source` 列で確かめられます。

社内の共有機では、ボット専用の Windows ユーザーを作り、管理者の PowerShell でタスクスケジューラーに登録します。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -User qa-bot -SshKey C:\qa-bot\keys\deploy_key
```

- `hacarus-check-qa-bot-sync`: 30 分ごとにミラーを同期する
- `hacarus-check-qa-bot`: 共有機の起動時にボットを立ち上げ、落ちたら再起動する。保存期間を過ぎた記録の削除も1日1回行う

## 情シスなどに依頼すること

- 社内の Slack で AI アプリ機能を使えるか(有料プランと管理者の設定)と、アプリの追加の許可
  - `slack/manifest.yml` をそのまま使える。Socket Mode なので、公開 URL やファイアウォールの穴あけは不要
- 共有機での実行と、ボット専用の Windows ユーザーの作成
- hacarus-check-2025 への読み取り専用の Deploy key の登録(リポジトリの管理者)
- Anthropic Console の API キーと、月の上限額の設定

## 設定

`.env.example` に一覧と説明があります。主なもの:

| 変数 | 既定 | 説明 |
|---|---|---|
| `MODEL` | `claude-sonnet-5-5` | 使うモデル |
| `MAX_TURNS` / `MAX_BUDGET_USD` | `30` / `2.0` | 1つの質問で許す往復回数と料金の上限 |
| `DAILY_LIMIT_PER_USER` | `20` | 1人が1日に質問できる件数 |
| `RETENTION_DAYS` / `SESSION_RETENTION_DAYS` | `365` / `30` | 記録と会話セッションの保存日数 |
| `DENY_PATHS` | `.git/**` | 参照を禁止するパス |

回答の口調や調べ方は `config/system_prompt.md` で、よくある質問は `knowledge/faq.md` で調整します。

## 開発

```powershell
.venv\Scripts\pytest
```

テストは Claude と Slack を呼ばずに動きます。Git のテスト用リポジトリはテストの中で作ります。
