# One-line install (Windows PowerShell):
#   irm https://raw.githubusercontent.com/wivrix/telecursor/main/install.ps1 | iex
#
# Or from a clone:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:TELECURSOR_REPO) { $env:TELECURSOR_REPO } else { "https://github.com/wivrix/telecursor.git" }
$InstallDir = if ($env:TELECURSOR_DIR) { $env:TELECURSOR_DIR } else { Join-Path $HOME "telecursor" }
$Branch = if ($env:TELECURSOR_BRANCH) { $env:TELECURSOR_BRANCH } else { "main" }

Write-Host "==> Telecursor installer"

function Get-LocalRoot {
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
        return $PSScriptRoot
    }
    return $null
}

$Root = Get-LocalRoot
if (-not $Root) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is required. Install Git for Windows, then re-run."
    }
    if (Test-Path (Join-Path $InstallDir ".git")) {
        Write-Host "==> Updating existing clone at $InstallDir"
        git -C $InstallDir fetch --depth 1 origin $Branch
        git -C $InstallDir checkout $Branch
        git -C $InstallDir pull --ff-only origin $Branch 2>$null
    } else {
        Write-Host "==> Cloning $RepoUrl -> $InstallDir"
        $parent = Split-Path -Parent $InstallDir
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent | Out-Null
        }
        git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
    }
    $Root = $InstallDir
}

Set-Location $Root
Write-Host "==> Project: $Root"

$Python = $null
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Creating virtualenv (.venv)"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv (Join-Path $Root ".venv")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python -m venv (Join-Path $Root ".venv")
    } else {
        throw "Python 3.10+ not found. Install from https://www.python.org/downloads/ (enable Add to PATH)."
    }
}
$Python = (Resolve-Path $VenvPython).Path

Write-Host "Using Python: $Python"
Write-Host "==> Installing dependencies"
& $Python -m pip install -U pip setuptools wheel
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
& $Python -m pip install -e $Root

Write-Host "==> Registering telecursor command"
& $Python (Join-Path $Root "main.py") install --system

$Scripts = Split-Path -Parent $Python
$Tele = Join-Path $Scripts "telecursor.exe"
if (Test-Path $Tele) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$Scripts*") {
        Write-Host "Adding $Scripts to User PATH..."
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Scripts", "User")
        $env:Path = "$env:Path;$Scripts"
    }
}

Write-Host ""
Write-Host "Telecursor installed"
Write-Host "  Folder:  $Root"
Write-Host "  Command: $Tele"
Write-Host ""
Write-Host "Next (open a new terminal if needed):"
Write-Host "  telecursor setup"
Write-Host "  telecursor start -d"
Write-Host "  telecursor status"
