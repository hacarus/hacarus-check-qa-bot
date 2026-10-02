# Slack アプリの作り方(検証用)

社内の Slack に、自分だけが使う試験用アプリを作り、ボットを起動するまでの手順です。
アプリの仕組みと権限の説明は [slack-app.md](slack-app.md) にあります。

画面の名前(英語)は Slack の公式の Bolt の手順書に合わせています。手順の 11 の承認の流れは、公式の資料で確かめられていません。画面が違ったら、そのときの表示を控えてください。

## 全体の流れ

| 段階 | やること | 情シスの了承 |
| --- | --- | --- |
| A. 作る | 手順 1〜9。アプリの設定を作り、接続用のトークンを発行する | 要らない(この時点のアプリはワークスペースに入っておらず、何もできない) |
| B. 依頼する | 手順 10。情シスに依頼文を送る | ここでもらう |
| C. 入れる | 手順 11〜14。インストールし、ボットを起動する | もらってから行う |

アプリを「作る」だけなら、ボットはワークスペースに現れず、メッセージを読むことも投稿することもできません。そうしたことができるようになるのは、インストールしてからです。

## A. 作る

### 1. アプリの管理画面を開く

1. ブラウザで会社の Slack にログインしておく
2. https://api.slack.com/apps を開く
3. 右上の **Create New App** を押す

### 2. マニフェストから作ることを選ぶ

**From a manifest**(「From an app manifest」と表示されることもある)を選びます。**From scratch** ではありません。

マニフェストは、アプリの名前、権限、受け取るイベントなどをまとめた設定ファイルです。このリポジトリの `slack/manifest.yml` を使うと、手で設定する項目がほぼなくなります。

### 3. ワークスペースを選ぶ

1. 一覧から**会社のワークスペース**を選ぶ
2. **Next** を押す

会社のワークスペースが一覧に出ない場合は、管理者がアプリを作れるメンバーを制限しています。その場合はここでやめ、情シスにアプリの作成から依頼します(依頼文の「お願い」を「manifest からアプリを作ってインストールしてください」に変える)。

### 4. マニフェストを貼る

1. **YAML** のタブを選ぶ(JSON のタブではない)
2. 入力欄に最初から入っている内容を全部消す
3. `slack/manifest.yml` の中身を全部コピーして貼る
4. **Next** を押す

エラーが出たら、表示された文をそのまま控えてください。

### 5. 内容を確かめて作る

設定の概要が表示されます。次のとおりになっていることを確かめて、**Create** を押します。

- Bot Scopes(10個): `app_mentions:read`、`chat:write`、`im:history`、`channels:history`、`groups:history`、`channels:read`、`groups:read`、`commands`、`files:read`、`usergroups:read`
- Bot Events(2つ): `app_mention`、`message.im`
- Socket Mode、Interactivity、Slash Commands(`/qa-cost`)、App Home、Bot User がある

### 6. Basic Information が開く

作ると、アプリの **Basic Information** のページが開きます。この時点では、まだインストールされていません。

### 7. 接続用のトークン(App-Level Token)を作る

ボットの PC が Slack に接続するときに使うトークンです。インストールの前でも作れます。

1. **Basic Information** を下にスクロールし、**App-Level Tokens** の **Generate Token and Scopes** を押す
2. Token Name に `socket` などの名前を入れる
3. **Add Scope** で `connections:write` を選ぶ
4. **Generate** を押す
5. `xapp-` で始まるトークンをコピーし、`.env` の `SLACK_APP_TOKEN=` に貼る

トークンはパスワードと同じ扱いです。チャットやメールに貼らず、`.env` 以外には保存しないでください。漏れた場合は、同じ画面でトークンを消して作り直せば、古いものは使えなくなります。

### 8. 設定を確かめる

左のメニューで、次のとおりになっていることを確かめます。マニフェストで設定済みなので、通常は何も変えなくてよいです。

- **Socket Mode**: Enable Socket Mode がオン
- **Event Subscriptions**: Enable Events がオン。Subscribe to bot events に `app_mention` と `message.im`
- **Interactivity & Shortcuts**: オン
- **Slash Commands**: `/qa-cost` がある
- **App Home**: Messages Tab がオンで、「Allow users to send Slash commands and messages from the messages tab」にチェックがある(これがないと DM を送れない)

### 9. (任意)アイコンを付ける

**Basic Information** の **Display Information** で、アプリのアイコンと色を設定できます。社内でボットを見分けやすくなります。

## B. 依頼する

### 10. 情シスに依頼文を送る

ここで止めます。**Install App** はまだ押さないでください。会社の設定によっては、押した瞬間にインストールが済んでしまうためです。

依頼文を送り、了承をもらってから次へ進みます。

## C. 入れる

### 11. インストールする

左のメニューの **Install App** を開きます。ボタンの表示によって、操作が変わります。

- **Install to (ワークスペース名)** と出る場合: 押すと、権限の確認画面が出るので **許可する(Allow)** を押す。これでインストールが済む
- **Request to Install** と出る場合: 管理者の承認が必要な設定です。押して、理由の欄に「依頼文でご相談した相談ボットの試験用アプリです」などと書いて送る
  - 承認されると Slackbot から知らせが届く。もう一度 **Install App** を開き、インストールの操作を済ませる(承認だけで終わらず、この操作が要ることがある)

### 12. ボットのトークン(Bot User OAuth Token)をコピーする

インストールすると、**OAuth & Permissions** のページに **Bot User OAuth Token** が出ます。`xoxb-` で始まるトークンをコピーし、`.env` の `SLACK_BOT_TOKEN=` に貼ります。

### 13. 自分のメンバー ID を書く

1. Slack で自分のプロフィールを開く
2. 「⋯」から **メンバー ID をコピー** を選ぶ(`U` で始まる ID)
3. `.env` の `ALLOWED_SLACK_USERS=` と `ADMIN_SLACK_USERS=` に、その ID を1つだけ書く

`AUTH_MODE=subscription` のままなら、自分以外の ID やユーザーグループを書くとボットは起動しません。

### 14. 起動して確かめる

```powershell
task check   # 「Slack 連携: 有効」と出ることを確かめる
task slack   # ボットを起動する(止めるときは Ctrl+C)
```

1. Slack のサイドバーの **アプリ** から「HACARUS Check 相談窓口」を開き、DM で質問する(見つからなければ、上の検索で `check-qa` を探す)
2. 回答のスレッドで続けて質問し、前の質問を踏まえて答えるかを見る
3. スクリーンショットやログを添付して質問する
4. 試験用のチャンネルで `/invite @check-qa` してからメンションする
5. `/qa-cost` で集計が自分にだけ表示されるかを見る

試している間は、PC とボットを起動したままにしてください。止めている間に届いた質問には答えません。

## あとで行うこと

- **アプリの管理者を増やす**: アプリの設定は、作った人のアカウントに紐づきます。試験公開の前に、**Collaborators** にもう1人(情シスか開発の担当者)を足してください。作った人が異動や退職をしても、管理を続けられるようにするためです
- **権限を変えるとき**: `slack/manifest.yml` を直し、アプリの **App Manifest** のページに貼り直します。権限が増えるとインストールのやり直しが必要になり、承認もやり直しになります
- **やめるとき**: **Basic Information** の一番下の **Delete App** で消すと、トークンもすぐに使えなくなります
