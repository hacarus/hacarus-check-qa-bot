#!/bin/sh
# 質問対象のリポジトリを読み取り専用の認証情報でミラーし、一定間隔で最新に保つ
#   REPO_URL      : 例 git@github.com:hacarus/hacarus-check-2025.git(Deploy key は読み取り専用で登録する)
#   REPO_PATH     : ミラーの置き場所
#   BRANCH        : 追いかけるブランチ(既定 develop)
#   SYNC_INTERVAL : 秒。0 なら1回だけ同期して終わる(既定 1800)
#   SPARSE_EXCLUDE: ミラーに置かないパス(スペース区切り。例 "secrets/ customer-data/")
set -eu

: "${REPO_URL:?REPO_URL を指定してください}"
: "${REPO_PATH:?REPO_PATH を指定してください}"
BRANCH="${BRANCH:-develop}"
SYNC_INTERVAL="${SYNC_INTERVAL:-1800}"
SPARSE_EXCLUDE="${SPARSE_EXCLUDE:-}"

sync_once() {
    if [ ! -d "$REPO_PATH/.git" ]; then
        git clone --depth 1 --branch "$BRANCH" --no-checkout "$REPO_URL" "$REPO_PATH"
        if [ -n "$SPARSE_EXCLUDE" ]; then
            git -C "$REPO_PATH" sparse-checkout init --no-cone
            {
                echo '/*'
                for p in $SPARSE_EXCLUDE; do echo "!/$p"; done
            } > "$REPO_PATH/.git/info/sparse-checkout"
        fi
        git -C "$REPO_PATH" checkout "$BRANCH"
    fi
    git -C "$REPO_PATH" fetch --depth 1 origin "$BRANCH"
    git -C "$REPO_PATH" reset --hard "origin/$BRANCH"
    git -C "$REPO_PATH" clean -fdx
    echo "$(date '+%Y-%m-%d %H:%M:%S') $(git -C "$REPO_PATH" rev-parse --short HEAD) に同期しました"
}

while :; do
    sync_once || echo "同期に失敗しました。次の周期で再試行します" >&2
    [ "$SYNC_INTERVAL" -eq 0 ] && break
    sleep "$SYNC_INTERVAL"
done
