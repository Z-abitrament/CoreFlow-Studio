param(
    [string]$DistRoot = ".\dist\CoreFlowStudio",
    [int]$UiStartupSeconds = 10,
    [switch]$SkipSimulatorSmoke
)

$ErrorActionPreference = "Stop"

$ResolvedDistRoot = Resolve-Path $DistRoot
$GuiExe = Join-Path $ResolvedDistRoot "CoreFlowStudio.exe"
$ConsoleExe = Join-Path $ResolvedDistRoot "CoreFlowStudioConsole.exe"

function Assert-FileExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        throw "Expected packaged file was not found: $Path"
    }
}

function Invoke-ConsoleCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $Output = & $ConsoleExe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $Text = ($Output | Out-String).Trim()
        throw "CoreFlowStudioConsole.exe $($Arguments -join ' ') failed: $Text"
    }
    return ($Output | Out-String).Trim()
}

function Test-UiProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExePath,
        [string[]]$Arguments = @(),
        [switch]$CaptureOutput
    )

    $StdOut = Join-Path $env:TEMP "coreflow-ui-stdout-$([Guid]::NewGuid()).log"
    $StdErr = Join-Path $env:TEMP "coreflow-ui-stderr-$([Guid]::NewGuid()).log"
    $StartArgs = @{
        FilePath = $ExePath
        PassThru = $true
        WindowStyle = "Hidden"
    }
    if ($Arguments.Count -gt 0) {
        $StartArgs.ArgumentList = $Arguments
    }
    if ($CaptureOutput) {
        $StartArgs.RedirectStandardOutput = $StdOut
        $StartArgs.RedirectStandardError = $StdErr
    }

    $Process = Start-Process @StartArgs
    Start-Sleep -Seconds $UiStartupSeconds
    if ($Process.HasExited) {
        $OutText = if (Test-Path $StdOut) { Get-Content $StdOut -Raw } else { "" }
        $ErrText = if (Test-Path $StdErr) { Get-Content $StdErr -Raw } else { "" }
        throw "$ExePath exited during UI startup with code $($Process.ExitCode). STDOUT: $OutText STDERR: $ErrText"
    }

    Stop-Process -Id $Process.Id
    if ($CaptureOutput) {
        $ErrText = if (Test-Path $StdErr) { Get-Content $StdErr -Raw } else { "" }
        if (-not [string]::IsNullOrWhiteSpace($ErrText)) {
            throw "$ExePath wrote to stderr during UI startup: $ErrText"
        }
    }
    Remove-Item -LiteralPath $StdOut, $StdErr -ErrorAction SilentlyContinue
}

Assert-FileExists -Path $GuiExe
Assert-FileExists -Path $ConsoleExe
Assert-FileExists -Path (Join-Path $ResolvedDistRoot "README.md")
Assert-FileExists -Path (Join-Path $ResolvedDistRoot "USER_MANUAL.en.md")
Assert-FileExists -Path (Join-Path $ResolvedDistRoot "USER_MANUAL.zh-CN.md")
Assert-FileExists -Path (Join-Path $ResolvedDistRoot "_internal\pyside6.cp313-win_amd64.dll")
Assert-FileExists -Path (Join-Path $ResolvedDistRoot "_internal\shiboken6.cp313-win_amd64.dll")

$BuildInfo = Invoke-ConsoleCheck -Arguments @("--build-info")
Write-Host $BuildInfo

if (-not $SkipSimulatorSmoke) {
    $SmokeRoot = Join-Path $ResolvedDistRoot "verify-smoke-data"
    $Smoke = Invoke-ConsoleCheck -Arguments @("--simulator-smoke", "--data-root", $SmokeRoot)
    Write-Host $Smoke
}

Test-UiProcess -ExePath $ConsoleExe -Arguments @("--ui") -CaptureOutput
Write-Host "Console UI startup check passed."

Test-UiProcess -ExePath $GuiExe
Write-Host "Windowed UI startup check passed."

Write-Host "Package verification passed."
