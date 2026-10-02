# hacarus-check-qa-bot(ZERO QA Bot)

営業や技術サポートのメンバーが、HACARUS Check 2025(ZERO)のリポジトリについて Slack で質問できるボットです。
回答には Claude Agent SDK を使い、リポジトリは読むことしかできないようにしています。

- Slack でボットに DM するか、チャンネルでメンションすると答える。スレッドで返信すると会話が続く
- 質問の文中でバージョンを指定でき(「v3.2.1 で…」「v3.2.1 から v3.3.2 に上げると…」)、スクリーンショットやログも添付できる
- 回答ごとに 👍 / 👎 で評価でき、質問ごとに API の従量課金だった場合の料金(仮想料金)を記録する

いまは「検証」の段階で、作成者の PC で作成者だけが使っています。
設計、運用、Slack アプリなどの文書は [docs/](docs/README.md) にあります。

## 手元で動かす

Windows の PowerShell で実行します。Python 3.11 以上、Git for Windows、[Task](https://taskfile.dev)(`winget install Task.Task`)、Claude Code が必要です。
コマンドは `Taskfile.yml` にまとめてあり、`task` だけを実行すると一覧が出ます。

```powershell
git clone https://github.com/hacarus/hacarus-check-qa-bot
cd hacarus-check-qa-bot
task setup       # .venv の作成、依存関係のインストール、.env の作成(AUTH_MODE=subscription のままにする)
task sync        # 質問対象のミラーを作る(自分の PC の git の認証で取得する。2回目以降は差分だけ取得する)
task check       # 設定とツール制限を確認する(Claude は呼ばない)
task ask -- v3.2.1 から v3.3.2 に上げるとき、設定の移行は必要ですか？
task chat        # 対話形式で質問する(/new で新しい会話、/cost で集計)
task slack       # Slack につなぐ(先に docs/slack-app-setup.md の手順でアプリを作る)
```

- 手元で Claude Code にログイン済みなら、そのアカウントの使用枠で動きます
- `task ask FAKE=1 -- 質問` とすると、Claude を呼ばずにダミーの回答を返します。記録や集計の流れだけを確かめるときに使います
- 設定の一覧と説明は `.env.example` にあります
- うまく動かないときは [docs/operations.md の「困ったとき」](docs/operations.md#困ったとき) を見てください

## 開発

```powershell
task test
```

テストは Claude と Slack を呼ばずに動きます。Git のテスト用リポジトリはテストの中で作ります。
