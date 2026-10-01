# hacarus-check-qa-bot

営業や技術サポートのメンバーが、HACARUS Check 2025 のリポジトリについて Slack で気軽に質問できるボットです。
回答には Claude Agent SDK(Claude Code と同じエージェント)を使い、リポジトリは読むことしかできないようにしています。

## 読み取り専用にする仕組み

次の層で重ねて制限しています。どれか1つの設定を誤っても、リポジトリは変更されません。

- エージェントに渡すツールは Read / Grep / Glob だけ。Bash や Edit などは存在しない状態で起動する
- すべてのツール呼び出しの直前に、パスがリポジトリ内かを検査する(`src/qa_bot/guard.py`)
  - Read に絶対パスを渡して認証情報や `/proc/self/environ` を読む、シンボリックリンクで外へ出る、といった抜け道を塞ぐ
  - `.git/` など `DENY_PATHS` に書いたパスも拒否する
- 対象リポジトリの `.claude/` や `CLAUDE.md`、`~/.claude` の設定(hooks を含む)は読み込まない
- 公開フェーズでは、リポジトリを読み取り専用の Deploy key でミラーし、コンテナには読み取り専用でマウントする

見せたくないディレクトリがある場合は、`DENY_PATHS` よりも `SPARSE_EXCLUDE` でミラーに置かないほうが確実です(Grep の検索結果にも出なくなるため)。

## 動作確認フェーズ(自分の使用枠で、自分だけが使う)

Slack なしで、手元の CLI から質問できます。

```sh
# 質問対象のミラーを用意する(開発用の作業コピーとは別にする)
git clone --depth 1 --branch develop git@github.com:hacarus/hacarus-check-2025.git ../hacarus-check-2025-mirror

python -m venv .venv
source .venv/bin/activate        # Windows は .venv\Scripts\Activate.ps1
pip install -e '.[dev]'

cp .env.example .env             # AUTH_MODE=subscription のままにする
qa-bot check                     # 設定とツール制限を確認する(Claude は呼ばない)
qa-bot chat                      # 対話形式で質問する
qa-bot ask "検査結果の CSV はどこに保存される？"
```

- 手元で Claude Code にログイン済みなら、そのアカウントの使用枠で動きます。サーバで動かす場合は `claude setup-token` で発行したトークンを `CLAUDE_CODE_OAUTH_TOKEN` に設定します
- `AUTH_MODE=subscription` のときは、Slack の利用者に自分以外を指定すると起動を拒否します。個人の使用枠をほかの人に使わせると規約違反になるためです
- `--fake` を付けると Claude を呼ばずにダミーの回答を返すので、記録や集計の流れだけを確かめられます

### 仮想料金の確認

質問ごとに、モデル別のトークン数(入力・出力・キャッシュ書き込み・キャッシュ読み出し)を `data/qa_bot.sqlite3` に記録します。
API の従量課金だったら幾らかを `config/pricing.toml` の単価で計算します。

```sh
qa-bot cost                      # 今月の件数、1件あたりの平均・中央値・90%点、月の件数ごとの見込み
qa-bot cost --month 2026-10 --project 300,600,1500
qa-bot export-csv -o usage.csv   # 稟議の資料用に CSV で書き出す(Excel で開ける)
```

`chat` の中では `/cost` で同じ集計を見られます。
使用枠でも API でもトークンの数え方は同じなので、ここで測った数字がそのまま公開後の見積もりになります。
なお、キャッシュ書き込みは 5 分 TTL の単価(入力の 1.25 倍)で計算しています。

## 公開フェーズへの切り替え

コードは変えず、`.env` だけを差し替えます。

```sh
AUTH_MODE=api
ANTHROPIC_API_KEY=sk-ant-...     # Console でワークスペースの月の上限額も設定する
CLAUDE_CODE_OAUTH_TOKEN=         # 空にする
ALLOWED_SLACK_USERS=*            # または質問できる人の ID をカンマ区切りで
```

`AUTH_MODE=api` なのに API キーがない場合や、`AUTH_MODE=subscription` なのに API キーが残っている場合も起動を拒否します。
どちらの認証で動いたかは質問ごとに記録しているので、`qa-bot export-csv` の `auth_source` 列で確かめられます。

サーバでは `docker compose up -d` で、ミラーの同期(30 分ごと)とボットが動きます。`keys/deploy_key` に読み取り専用の Deploy key を置いてください。

## 情シスに依頼すること

- Slack アプリの作成: `slack/manifest.yml` を「From an app manifest」に貼り付ければ作れます
  - 発行してほしいもの: Bot User OAuth Token(`xoxb-`)と App-Level Token(`xapp-`、スコープは `connections:write`)
  - Socket Mode を使うので、公開 URL やファイアウォールの穴あけは不要です
  - 必要な権限は、メンションの読み取り、DM の読み取り、投稿、スラッシュコマンドだけです
- 動かすサーバ(Docker が動く小さな VM で十分です)
- GitHub の読み取り専用 Deploy key の登録(hacarus-check-2025 の管理者)
- Anthropic Console の API キーと、月の上限額の設定

Slack では、ボットへのメンションか DM で質問します。同じスレッドで続けて質問すると、前の会話を踏まえて答えます。
`/qa-cost` は `ADMIN_SLACK_USERS` に書いた人だけが使え、仮想料金の集計を本人にだけ表示します。

## 設定

`.env.example` に一覧と説明があります。主なもの:

| 変数 | 既定 | 説明 |
|---|---|---|
| `MODEL` | `claude-sonnet-5-5` | 使うモデル |
| `MAX_TURNS` / `MAX_BUDGET_USD` | `30` / `2.0` | 1つの質問で許す往復回数と料金の上限 |
| `DENY_PATHS` | `.git/**` | 参照を禁止するパス |
| `LOG_QUESTION_TEXT` | `true` | 質問文も記録するか |

回答の口調や調べ方は `config/system_prompt.md` で調整します。

## 開発

```sh
pytest
```

テストは Claude と Slack を呼ばずに動きます(ダミーのエージェントと Slack クライアントを使う)。
