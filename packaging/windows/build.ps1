param(
    [string]$BuildChannel = "local",
    [string]$CondaEnv = "coreflow-studio",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$env:PYTHONNOUSERSITE = "1"

function Resolve-CondaPython {
    if ($env:CONDA_DEFAULT_ENV -eq $CondaEnv) {
        return (Get-Command python -ErrorAction Stop).Source
    }

    $Conda = Get-Command conda -ErrorAction SilentlyContinue
    if (-not $Conda) {
        throw "Conda was not found. Create the project environment with: conda env create -f environment.yml"
    }

    $EnvironmentJson = (& conda env list --json) | Out-String
    $EnvironmentInfo = $EnvironmentJson | ConvertFrom-Json
    $EnvironmentPath = $EnvironmentInfo.envs | Where-Object {
        (Split-Path $_ -Leaf) -eq $CondaEnv
    } | Select-Object -First 1
    if (-not $EnvironmentPath) {
        throw "Conda environment '$CondaEnv' was not found. Create it with: conda env create -f environment.yml"
    }

    $Python = Join-Path $EnvironmentPath "python.exe"
    if (-not (Test-Path $Python)) {
        throw "Expected Python executable at $Python"
    }
    return $Python
}

$Python = Resolve-CondaPython

function Invoke-ProjectPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Assert-DistNotRunning {
    $DistRoot = Join-Path $RepoRoot "dist\CoreFlowStudio"
    if (-not (Test-Path $DistRoot)) {
        return
    }

    $RunningProcesses = Get-Process | Where-Object {
        $_.Path -and $_.Path.StartsWith($DistRoot, [System.StringComparison]::OrdinalIgnoreCase)
    }
    if ($RunningProcesses) {
        $Details = ($RunningProcesses | ForEach-Object {
            "$($_.ProcessName) pid=$($_.Id)"
        }) -join ", "
        throw "Close running packaged CoreFlow Studio processes before building: $Details"
    }
}

Push-Location $RepoRoot
try {
    if (-not $SkipTests) {
        $TestTemp = Join-Path $RepoRoot "pytest-temp"
        $TestCache = Join-Path $RepoRoot "pytest-cache-temp"
        $env:TMP = $RepoRoot
        $env:TEMP = $RepoRoot
        Invoke-ProjectPython -Arguments @("-m", "pytest", "--basetemp=$TestTemp", "-o", "cache_dir=$TestCache")
    }

    Assert-DistNotRunning

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

    Invoke-ProjectPython -Arguments @("-m", "PyInstaller", "--noconfirm", ".\packaging\windows\coreflow_studio.spec")

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
    if (Test-Path $BuildStampHook) {
        Remove-Item -LiteralPath $BuildStampHook -Force
    }

    Write-Host "Built dist\CoreFlowStudio\CoreFlowStudio.exe"
    Write-Host "Built dist\CoreFlowStudio\CoreFlowStudioConsole.exe"
}
finally {
    if ($BuildStampHook -and (Test-Path $BuildStampHook)) {
        Remove-Item -LiteralPath $BuildStampHook -Force
    }
    Pop-Location
}
