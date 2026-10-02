# 運用の手順

ボットを動かし、回答と費用を見直し、公開へ切り替えるための手順です。
作業の頻度と、なぜそうするかは [design.md の「運用」](design.md#運用) にあります。

## 仮想料金と評価の確認

```powershell
task cost                                        # 今月の件数・評価・1件あたりの平均と最大、月の件数ごとの見込み
task cost -- --month 2026-10 --project 300,600,1500
task export                                      # 稟議の資料用に usage.csv へ書き出す(Excel で開ける)
```

- Slack では `/qa-cost` で同じ集計を見られます。使えるのは `ADMIN_SLACK_USERS` に書いた人とグループのメンバーだけで、結果は打った本人にだけ表示されます
- 利用者別の欄は、`/qa-cost` では名前、`task cost` では Slack の ID で出ます(ボットには ID から名前を調べる権限がないため)。`cli` は手元の PC から `task ask` や `task chat` で質問した分です
- 使用枠でも API でもトークンの数え方は同じなので、ここで測った数字がそのまま公開後の見積もりになります
- キャッシュ書き込みは 5 分 TTL の単価(入力の 1.25 倍)で計算しています。単価は `config/pricing.toml` にあり、Anthropic が料金を改定したら直します
- 👎 の理由は `task export` の `bad_reasons` 列で読めます

## 回答の直し方

- 繰り返し聞かれる質問や、間違えやすい質問の正しい答えは `knowledge/faq.md` に足します。ボットは最初に FAQ を引きます
- 口調、調べ方、回答の書き方を変えるときは `config/system_prompt.md` を直します
- どちらも質問のたびに読み直すので、ボットを起動し直さなくても次の質問から反映されます

## 公開への切り替え

コードは変えず、`.env` だけを差し替えます。

```
AUTH_MODE=api
ANTHROPIC_API_KEY=(Console で発行したキー。Console でワークスペースの月の上限額も設定する)
CLAUDE_CODE_OAUTH_TOKEN=         # 空にする
ALLOWED_SLACK_USERS=@sales,U...  # 質問できる人の ID かユーザーグループ(* で全員)
```

- 次の組み合わせでは、ボットは起動を拒否します。個人の使用枠をほかの人に使わせると規約違反になることと、気づかずに課金されるのを防ぐためです
  - `AUTH_MODE=subscription` なのに、Slack の利用者に自分以外を指定している
  - `AUTH_MODE=subscription` なのに、API キーが残っている
  - `AUTH_MODE=api` なのに、API キーがない
- どちらの認証で動いたかは質問ごとに記録しており、`task export` の `auth_source` 列で確かめられます

## 共有機への登録

社内の共有機では、ボット専用の Windows ユーザーを作り、管理者の PowerShell でタスクスケジューラーに登録します。

```powershell
task sync REPO_URL=git@github.com:hacarus/hacarus-check-2025.git SSH_KEY=C:\qa-bot\keys\deploy_key
task register USER=qa-bot SSH_KEY=C:\qa-bot\keys\deploy_key
```

- `hacarus-check-qa-bot-sync`: 30 分ごとにミラーを同期する
- `hacarus-check-qa-bot`: 共有機の起動時にボットを立ち上げ、落ちたら再起動する。保存期間を過ぎた記録の削除も1日1回行う
- 登録したタスクは Task を使わず `qa-bot.exe` と同期スクリプトを直接起動するので、共有機の常駐に Task は要らない

## 情シスなどに依頼すること

| 依頼 | 相手 | 段階 |
|---|---|---|
| 社内の Slack へのアプリの追加の許可(`slack/manifest.yml` をそのまま使える。Socket Mode なので、公開 URL やファイアウォールの穴あけは不要) | 情シス | 検証(済み) |
| 共有機での実行と、ボット専用の Windows ユーザーの作成 | 情シス | 試験公開の前 |
| hacarus-check-2025 への読み取り専用の Deploy key の登録 | リポジトリの管理者 | 試験公開の前 |
| Anthropic Console の API キーと、月の上限額の設定 | 稟議の承認者 | 試験公開の前 |
| Slack アプリの Collaborators への追加(作成者が異動しても管理を続けられるように) | 情シスか開発の担当者 | 試験公開の前 |

## 困ったとき

- **ボットが答えない**: ボットは `task slack`(共有機ではタスクスケジューラー)が動いている間だけ答えます。PC がスリープしていないか、PowerShell を閉じていないかを確かめてください。止めている間に届いた質問には、あとから答えません
- **`[claude-code:unrecognized_model]` と出る**: Windows 向けの claude-agent-sdk は Claude Code(claude.exe)を同梱していない版があり、その場合は PC にインストールした Claude Code が使われます。`task check` の「Claude Code」の行で版を確かめ、古いと出たら `claude update` で更新してください
- **起動を拒否される**: 「公開への切り替え」に書いた組み合わせになっていないかを確かめてください。`task check` を実行すると「設定エラー」として理由が出ます
- **「利用できるメンバーを限定しています」と返る**: 質問した人が `ALLOWED_SLACK_USERS` に入っていません。ユーザーグループのメンバーは10分ごとに読み直すので、グループに足した直後は少し待ってください
- **👎 の理由の入力欄が開かない**: 評価は記録されています。理由も書く場合は、もう一度 👎 を押してください
