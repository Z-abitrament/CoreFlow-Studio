param(
    [string]$BuildChannel = "local",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Expected virtual environment Python at $Python"
}

Push-Location $RepoRoot
try {
    if (-not $SkipTests) {
        $TestTemp = Join-Path $RepoRoot "pytest-temp"
        $TestCache = Join-Path $RepoRoot "pytest-cache-temp"
        $env:TMP = $RepoRoot
        $env:TEMP = $RepoRoot
        & $Python -m pytest --basetemp=$TestTemp -o "cache_dir=$TestCache"
    }

    $env:COREFLOW_BUILD_CHANNEL = $BuildChannel
    $GitCommit = (git rev-parse --short HEAD)
    if ((git status --short)) {
        $GitCommit = "$GitCommit-dirty"
    }
    $env:COREFLOW_BUILD_COMMIT = $GitCommit
    $BuildStampHook = Join-Path $ScriptDir "generated_build_stamp.py"
@"
import os

os.environ.setdefault("COREFLOW_PACKAGED", "1")
os.environ.setdefault("COREFLOW_BUILD_CHANNEL", "$BuildChannel")
os.environ.setdefault("COREFLOW_BUILD_COMMIT", "$env:COREFLOW_BUILD_COMMIT")
"@ | Set-Content -LiteralPath $BuildStampHook -Encoding UTF8

    & $Python -m PyInstaller --noconfirm .\packaging\windows\coreflow_studio.spec

    $ReadmeSource = Join-Path $ScriptDir "README.md"
    $ReadmeTarget = Join-Path $RepoRoot "dist\CoreFlowStudio\README.md"
    Copy-Item -LiteralPath $ReadmeSource -Destination $ReadmeTarget -Force
    Copy-Item `
        -LiteralPath (Join-Path $RepoRoot "docs\USER_MANUAL.en.md") `
        -Destination (Join-Path $RepoRoot "dist\CoreFlowStudio\USER_MANUAL.en.md") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $RepoRoot "docs\USER_MANUAL.zh-CN.md") `
        -Destination (Join-Path $RepoRoot "dist\CoreFlowStudio\USER_MANUAL.zh-CN.md") `
        -Force
    Remove-Item -LiteralPath $BuildStampHook -Force

    Write-Host "Built dist\CoreFlowStudio\CoreFlowStudio.exe"
}
finally {
    if ($BuildStampHook -and (Test-Path $BuildStampHook)) {
        Remove-Item -LiteralPath $BuildStampHook -Force
    }
    Pop-Location
}
