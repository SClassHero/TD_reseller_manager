param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

if (-not $SkipInstall) {
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install pyinstaller
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --name TDResellerManager `
    --add-data "templates;templates" `
    --add-data "static;static" `
    run_desktop.py

$DistDir = Join-Path $Root "dist\TDResellerManager"
$DataDir = Join-Path $DistDir "data"
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "backups") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "uploads\products") | Out-Null

@"
@echo off
cd /d "%~dp0"
start "" "TDResellerManager.exe"
"@ | Set-Content -Path (Join-Path $DistDir "Start TD Reseller Manager.bat") -Encoding ASCII

Write-Host ""
Write-Host "Portable build created:"
Write-Host $DistDir
Write-Host ""
Write-Host "Zip and share that folder. User data will live in its data\ folder."
