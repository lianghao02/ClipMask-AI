# UTF-8 Compatibility
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$projectDir = $PSScriptRoot
$reqFile = Join-Path $projectDir 'requirements.txt'

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "🛡️ ClipMask-AI 環境配置與啟動程序" -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $reqFile)) {
    throw "找不到 requirements.txt: $reqFile"
}

$pyCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pyCommand) {
    throw "系統中未偵測到 Python，請先安裝 Python 3.11+"
}

Write-Host "📦 正在檢查並安裝依賴套件..." -ForegroundColor Yellow
$pyExe = $pyCommand.Source
& $pyExe -m pip install -r $reqFile --quiet

if ($LASTEXITCODE -ne 0) {
    throw "依賴套件安裝失敗，請檢查網路連線。"
}

Write-Host "✅ 環境已就緒！" -ForegroundColor Green

if (-not $NoLaunch) {
    Write-Host "🚀 正在啟動 ClipMask-AI..." -ForegroundColor Cyan
    $runPy = Join-Path $projectDir 'run.py'
    & $pyExe $runPy
}