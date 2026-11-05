$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "data\logs"
$python = Join-Path $root ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Set-Location $root
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:PYTHONUNBUFFERED = "1"
$env:OMP_NUM_THREADS = "4"

while ($true) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path (Join-Path $logDir "supervisor.log") -Value "[$stamp] starting uvicorn"
    $proc = Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning" `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $logDir "server.out.log") `
        -RedirectStandardError (Join-Path $logDir "server.err.log") `
        -WindowStyle Hidden -PassThru
    Wait-Process -Id $proc.Id
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path (Join-Path $logDir "supervisor.log") -Value "[$stamp] uvicorn stopped, restarting in 10s"
    Start-Sleep -Seconds 10
}
