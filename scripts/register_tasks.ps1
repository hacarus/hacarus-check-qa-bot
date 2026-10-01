# ボットとミラーの同期を、タスクスケジューラーに登録する(管理者の PowerShell で実行する)
#
# 例:
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -User qa-bot
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -User qa-bot -SshKey C:\qa-bot\keys\deploy_key
#
# -User には、ボット専用に作った Windows ユーザーを指定する(登録時にパスワードを聞かれる)。
# そのユーザーには、このフォルダの読み取りと data フォルダへの書き込みだけを許可しておく。
param(
    [Parameter(Mandatory = $true)][string]$User,
    [string]$SshKey = "",
    [int]$SyncMinutes = 30
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $root ".venv\Scripts\qa-bot.exe"
if (-not (Test-Path $python)) { throw "$python がありません。先に README の手順で .venv を作ってください" }
$credential = Get-Credential -UserName $User -Message "タスクを実行するユーザーのパスワード"
$password = $credential.GetNetworkCredential().Password

# ミラーの同期: 起動時と、その後は一定間隔で
$syncArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\sync_repo.ps1`""
if ($SshKey) { $syncArgs += " -SshKey `"$SshKey`"" }
$syncAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $syncArgs -WorkingDirectory $root
$syncTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $SyncMinutes)
Register-ScheduledTask -TaskName "hacarus-check-qa-bot-sync" -Action $syncAction -Trigger $syncTrigger `
    -User $User -Password $password -Force | Out-Null

# ボット本体: 起動時に立ち上げ、落ちたら再起動する
$botAction = New-ScheduledTaskAction -Execute $python -Argument "slack" -WorkingDirectory $root
$botTrigger = New-ScheduledTaskTrigger -AtStartup
$botSettings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName "hacarus-check-qa-bot" -Action $botAction -Trigger $botTrigger `
    -Settings $botSettings -User $User -Password $password -Force | Out-Null

Write-Host "登録しました: hacarus-check-qa-bot-sync($SyncMinutes 分ごと)、hacarus-check-qa-bot(起動時)"
Write-Host "今すぐ始めるには: Start-ScheduledTask hacarus-check-qa-bot-sync; Start-ScheduledTask hacarus-check-qa-bot"
