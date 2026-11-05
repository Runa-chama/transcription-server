$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir "tunnel.log"

while ($true) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "[$stamp] connecting to ken"
    & ssh.exe -N `
        -o BatchMode=yes `
        -o ExitOnForwardFailure=yes `
        -o ConnectTimeout=15 `
        -o ServerAliveInterval=20 `
        -o ServerAliveCountMax=3 `
        -o TCPKeepAlive=yes `
        -R 127.0.0.1:36419:127.0.0.1:8000 `
        ken 2>&1 | Out-File -Append -Encoding utf8 $logPath
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "[$stamp] tunnel closed, reconnecting in 10s"
    Start-Sleep -Seconds 10
}
