# Install the `telecursor` command on Windows (PowerShell).
# Usage:  powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = $null
if (Test-Path "$Root\.venv\Scripts\python.exe") {
    $Python = "$Root\.venv\Scripts\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
} else {
    Write-Error "Python not found. Install Python 3.10+ from https://www.python.org/downloads/ (enable Add to PATH)."
}

Write-Host "Using Python: $Python"
& $Python -m pip install -U pip setuptools wheel
& $Python -m pip install -e $Root
& $Python "$Root\main.py" install --system

$Scripts = Split-Path -Parent (Resolve-Path $Python).Path
$Tele = Join-Path $Scripts "telecursor.exe"
if (Test-Path $Tele) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$Scripts*") {
        Write-Host ""
        Write-Host "Adding $Scripts to User PATH…"
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Scripts", "User")
        $env:Path = "$env:Path;$Scripts"
        Write-Host "Done. Open a new terminal, then run:  telecursor setup"
    } else {
        Write-Host "telecursor is ready: $Tele"
    }
}
