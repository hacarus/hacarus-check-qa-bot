# 質問対象のリポジトリを、作業ツリーを持たないミラー(bare リポジトリ)として同期する
#
# 例(初回もこのまま実行すればミラーを作る):
#   powershell -ExecutionPolicy Bypass -File scripts\sync_repo.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\sync_repo.ps1 -SshKey C:\qa-bot\keys\deploy_key
#
# 公開フェーズでは、GitHub に「Allow write access」を付けずに登録した Deploy key を -SshKey で渡す。
# 検証フェーズでは、自分の PC の git に設定済みの認証(HTTPS など)で取得してよい。
param(
    [string]$RepoUrl = "git@github.com:hacarus/hacarus-check-2025.git",
    [string]$MirrorPath = (Join-Path $PSScriptRoot "..\data\mirror.git"),
    [string]$Branch = "develop",
    [string]$SshKey = ""
)

$ErrorActionPreference = "Stop"
$MirrorPath = [System.IO.Path]::GetFullPath($MirrorPath)

if ($SshKey) {
    # Deploy key だけを使い、PC にある他の鍵や設定は使わない
    $env:GIT_SSH_COMMAND = "ssh -i `"$SshKey`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
}

if (-not (Test-Path (Join-Path $MirrorPath "HEAD"))) {
    Write-Host "ミラーを作成します: $MirrorPath"
    git init --bare --quiet $MirrorPath
    if ($LASTEXITCODE -ne 0) { throw "ミラーを作成できませんでした" }
    git --git-dir=$MirrorPath remote add origin $RepoUrl
}

# develop とすべてのタグを取得し、消えたタグは消す
git --git-dir=$MirrorPath fetch --quiet --prune --prune-tags origin `
    "+refs/heads/${Branch}:refs/heads/${Branch}" "+refs/tags/*:refs/tags/*"
if ($LASTEXITCODE -ne 0) { throw "同期に失敗しました" }

$head = git --git-dir=$MirrorPath rev-parse --short "refs/heads/$Branch"
$tags = (git --git-dir=$MirrorPath tag --list).Count
Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 同期しました($Branch=$head、タグ $tags 個)"
