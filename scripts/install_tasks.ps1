$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Register-AppTask {
    param(
        [string] $Name,
        [string] $Script,
        [string] $Description
    )
    $action = New-ScheduledTaskAction -Execute $powershell `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`"" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings `
        -Description $Description -Force | Out-Null
    Write-Output "registered: $Name"
}

Register-AppTask -Name "TranscriptionServer" `
    -Script (Join-Path $root "scripts\run_server.ps1") `
    -Description "Transcription web app (uvicorn on 127.0.0.1:8000)"
Register-AppTask -Name "TranscriptionTunnel" `
    -Script (Join-Path $root "scripts\run_tunnel.ps1") `
    -Description "Reverse SSH tunnel to ken (127.0.0.1:36419)"

Start-ScheduledTask -TaskName "TranscriptionServer"
Start-ScheduledTask -TaskName "TranscriptionTunnel"
Get-ScheduledTask -TaskName "TranscriptionServer", "TranscriptionTunnel" |
    Select-Object TaskName, State
