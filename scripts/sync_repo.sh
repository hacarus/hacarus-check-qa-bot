#!/bin/sh
# 質問対象のリポジトリを、作業ツリーを持たないミラー(bare リポジトリ)として同期する(Linux / Mac 用)
#   REPO_URL    : 既定 git@github.com:hacarus/hacarus-check-2025.git
#   MIRROR_PATH : 既定 data/mirror.git
#   BRANCH      : 既定 develop
#   SSH_KEY     : 読み取り専用の Deploy key(省略時は git の既定の認証)
set -eu

REPO_URL="${REPO_URL:-git@github.com:hacarus/hacarus-check-2025.git}"
MIRROR_PATH="${MIRROR_PATH:-$(dirname "$0")/../data/mirror.git}"
BRANCH="${BRANCH:-develop}"

if [ -n "${SSH_KEY:-}" ]; then
    export GIT_SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

if [ ! -f "$MIRROR_PATH/HEAD" ]; then
    git init --bare --quiet "$MIRROR_PATH"
    git --git-dir="$MIRROR_PATH" remote add origin "$REPO_URL"
fi

git --git-dir="$MIRROR_PATH" fetch --quiet --prune --prune-tags origin \
    "+refs/heads/$BRANCH:refs/heads/$BRANCH" "+refs/tags/*:refs/tags/*"
echo "$(date '+%Y-%m-%d %H:%M:%S') 同期しました($BRANCH=$(git --git-dir="$MIRROR_PATH" rev-parse --short "refs/heads/$BRANCH")、タグ $(git --git-dir="$MIRROR_PATH" tag --list | wc -l | tr -d ' ') 個)"
