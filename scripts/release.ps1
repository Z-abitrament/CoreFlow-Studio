param(
    [string]$Repository = "Z-abitrament/CoreFlow-Studio",
    [string]$Remote = "origin",
    [string]$Branch = "",
    [string]$CondaEnv = "coreflow-studio",
    [string]$BuildChannel = "local",
    [string]$PreviousVersion = "",
    [string]$PreviousPackage = "",
    [switch]$SkipBuild,
    [switch]$SkipBuildTests,
    [switch]$SkipVerify,
    [switch]$NoPush,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    $Display = "$FilePath $($Arguments -join ' ')".Trim()
    Write-Host "> $Display"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Display"
    }
}

function Invoke-NativeAllowFailure {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments *> $null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

function Assert-LastCommandSucceeded {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Read-ProjectVersion {
    $PyprojectPath = Join-Path $RepoRoot "pyproject.toml"
    $InitPath = Join-Path $RepoRoot "src\coreflow\__init__.py"

    $PyprojectText = Get-Content -LiteralPath $PyprojectPath -Raw
    $PyprojectMatch = [regex]::Match($PyprojectText, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $PyprojectMatch.Success) {
        throw "Unable to read project version from pyproject.toml"
    }

    $InitText = Get-Content -LiteralPath $InitPath -Raw
    $InitMatch = [regex]::Match($InitText, '__version__\s*=\s*"([^"]+)"')
    if (-not $InitMatch.Success) {
        throw "Unable to read package version from src\coreflow\__init__.py"
    }

    $PyprojectVersion = $PyprojectMatch.Groups[1].Value
    $PackageVersion = $InitMatch.Groups[1].Value
    if ($PyprojectVersion -ne $PackageVersion) {
        throw "Version mismatch: pyproject.toml has $PyprojectVersion but src\coreflow\__init__.py has $PackageVersion"
    }

    return $PyprojectVersion
}

function Resolve-GitHubCli {
    $Gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($Gh) {
        return $Gh.Source
    }

    if ($env:LOCALAPPDATA) {
        $LocalGh = Join-Path $env:LOCALAPPDATA "Programs\GitHubCLI\bin\gh.exe"
        if (Test-Path $LocalGh) {
            return $LocalGh
        }
    }

    throw "GitHub CLI was not found. Install gh and run: gh auth login"
}

function Assert-CleanTrackedTree {
    $Status = (& git status --short) | Out-String
    if (-not [string]::IsNullOrWhiteSpace($Status)) {
        throw "Commit or ignore working tree changes before release:`n$Status"
    }
}

function Assert-ReleaseDoesNotExist {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Gh,
        [Parameter(Mandatory = $true)]
        [string]$Tag
    )

    $LocalTag = (& git tag --list $Tag) | Out-String
    if (-not [string]::IsNullOrWhiteSpace($LocalTag)) {
        $TaggedCommit = (& git rev-list -n 1 $Tag).Trim()
        $HeadCommit = (& git rev-parse HEAD).Trim()
        if ($TaggedCommit -ne $HeadCommit) {
            throw "Local tag $Tag already exists and does not point to HEAD."
        }
    }

    $RemoteTag = (& git ls-remote --tags $Remote "refs/tags/$Tag*") | Out-String
    Assert-LastCommandSucceeded "Remote tag query"
    if (-not [string]::IsNullOrWhiteSpace($RemoteTag)) {
        $HeadCommit = (& git rev-parse HEAD).Trim()
        if ($RemoteTag -notmatch [regex]::Escape($HeadCommit)) {
            throw "Remote tag $Tag already exists on $Remote and does not point to HEAD."
        }
    }

    $ReleaseViewExitCode = Invoke-NativeAllowFailure -FilePath $Gh -Arguments @(
        "release",
        "view",
        $Tag,
        "--repo",
        $Repository
    )
    if ($ReleaseViewExitCode -eq 0) {
        throw "GitHub Release $Tag already exists."
    }
}

function Resolve-PreviousReleaseVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Gh,
        [Parameter(Mandatory = $true)]
        [string]$CurrentTag
    )

    if (-not [string]::IsNullOrWhiteSpace($PreviousVersion)) {
        return $PreviousVersion.TrimStart("v")
    }

    $LatestJson = (& $Gh release view --repo $Repository --json tagName 2>$null) | Out-String
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($LatestJson)) {
        return ""
    }

    $LatestRelease = $LatestJson | ConvertFrom-Json
    if ($LatestRelease.tagName -and $LatestRelease.tagName -ne $CurrentTag) {
        return ([string]$LatestRelease.tagName).TrimStart("v")
    }

    return ""
}

function Ensure-PreviousPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Gh,
        [string]$Version = ""
    )

    if ([string]::IsNullOrWhiteSpace($Version)) {
        return ""
    }

    if (-not [string]::IsNullOrWhiteSpace($PreviousPackage)) {
        $ResolvedPackage = (Resolve-Path -LiteralPath $PreviousPackage).Path
        return $ResolvedPackage
    }

    $UpdatesDir = Join-Path $RepoRoot "dist\updates"
    New-Item -ItemType Directory -Path $UpdatesDir -Force | Out-Null
    $ExpectedPackage = Join-Path $UpdatesDir "CoreFlowStudio-$Version-full.zip"
    if (Test-Path $ExpectedPackage) {
        return (Resolve-Path -LiteralPath $ExpectedPackage).Path
    }

    Write-Host "Previous full package was not found locally. Downloading v$Version asset..."
    Invoke-Checked -FilePath $Gh -Arguments @(
        "release",
        "download",
        "v$Version",
        "--repo",
        $Repository,
        "--pattern",
        "CoreFlowStudio-$Version-full.zip",
        "--dir",
        $UpdatesDir,
        "--clobber"
    )

    if (-not (Test-Path $ExpectedPackage)) {
        throw "Unable to find previous full package: $ExpectedPackage"
    }
    return (Resolve-Path -LiteralPath $ExpectedPackage).Path
}

function Remove-PackageVerificationArtifacts {
    $DistRoot = Join-Path $RepoRoot "dist\CoreFlowStudio"
    if (-not (Test-Path $DistRoot)) {
        return
    }

    $ResolvedDistRoot = (Resolve-Path -LiteralPath $DistRoot).Path
    $Names = @("verify-replay-data", "verify-smoke-data", "verify-replay-template.csv")
    foreach ($Name in $Names) {
        $Candidate = Join-Path $ResolvedDistRoot $Name
        $Resolved = Resolve-Path -LiteralPath $Candidate -ErrorAction SilentlyContinue
        if ($Resolved -and $Resolved.Path.StartsWith($ResolvedDistRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $Resolved.Path -Recurse -Force
        }
    }
}

function New-ReleaseNotes {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [string]$PreviousVersion = ""
    )

    $LogArgs = @("log", "--pretty=format:- %s")
    if (-not [string]::IsNullOrWhiteSpace($PreviousVersion)) {
        $PreviousTag = "v$PreviousVersion"
        $LocalPreviousTag = (& git tag --list $PreviousTag) | Out-String
        if (-not [string]::IsNullOrWhiteSpace($LocalPreviousTag)) {
            $LogArgs += "$PreviousTag..HEAD"
        }
    }

    $Changes = (& git @LogArgs) | Out-String
    if ([string]::IsNullOrWhiteSpace($Changes)) {
        $Changes = "- Release $Version."
    }

    return @"
CoreFlow Studio $Version

Changes:
$($Changes.Trim())

Update behavior:
- Target PCs read latest.json from the GitHub Release.
- A full update package is always uploaded.
- A patch package is uploaded when a compatible previous package is available.
- Clients prefer a matching patch package and fall back to the full package.
"@
}

Push-Location $RepoRoot
try {
    $Version = Read-ProjectVersion
    $Tag = "v$Version"
    $Gh = Resolve-GitHubCli
    if ([string]::IsNullOrWhiteSpace($Branch)) {
        $Branch = (& git branch --show-current).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($Branch)) {
        throw "Unable to resolve the current git branch."
    }

    Assert-CleanTrackedTree
    Assert-ReleaseDoesNotExist -Gh $Gh -Tag $Tag
    $ResolvedPreviousVersion = Resolve-PreviousReleaseVersion -Gh $Gh -CurrentTag $Tag

    Write-Host "Release plan:"
    Write-Host "  Repository: $Repository"
    Write-Host "  Branch:     $Branch"
    Write-Host "  Version:    $Version"
    Write-Host "  Tag:        $Tag"
    if (-not [string]::IsNullOrWhiteSpace($ResolvedPreviousVersion)) {
        Write-Host "  Previous:   $ResolvedPreviousVersion"
    } else {
        Write-Host "  Previous:   none"
    }

    if (-not $Yes) {
        $Answer = Read-Host "Publish this release? Type 'yes' to continue"
        if ($Answer -ne "yes") {
            throw "Release cancelled."
        }
    }

    if (-not $SkipBuild) {
        $BuildArgs = @(
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "packaging\windows\build.ps1",
            "-CondaEnv",
            $CondaEnv,
            "-BuildChannel",
            $BuildChannel
        )
        if ($SkipBuildTests) {
            $BuildArgs += "-SkipTests"
        }
        Invoke-Checked -FilePath "powershell" -Arguments $BuildArgs
    }

    $ConsoleExe = Join-Path $RepoRoot "dist\CoreFlowStudio\CoreFlowStudioConsole.exe"
    if (-not (Test-Path $ConsoleExe)) {
        throw "Packaged console executable was not found: $ConsoleExe"
    }

    if (-not $SkipVerify) {
        Invoke-Checked -FilePath "powershell" -Arguments @(
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "packaging\windows\verify_package.ps1"
        )
        Remove-PackageVerificationArtifacts
    }

    $PreviousPackagePath = Ensure-PreviousPackage -Gh $Gh -Version $ResolvedPreviousVersion
    $BaseUrl = "https://github.com/$Repository/releases/download/$Tag"
    $UpdateArgs = @(
        "--make-update-package",
        (Join-Path $RepoRoot "dist\CoreFlowStudio"),
        "--update-output-dir",
        (Join-Path $RepoRoot "dist\updates"),
        "--update-base-url",
        $BaseUrl
    )
    if (-not [string]::IsNullOrWhiteSpace($ResolvedPreviousVersion)) {
        $UpdateArgs += @("--previous-update-version", $ResolvedPreviousVersion)
        if (-not [string]::IsNullOrWhiteSpace($PreviousPackagePath)) {
            $UpdateArgs += @("--previous-update-package", $PreviousPackagePath)
        }
    }
    Invoke-Checked -FilePath $ConsoleExe -Arguments $UpdateArgs

    $UpdatesDir = Join-Path $RepoRoot "dist\updates"
    $FullPackage = Join-Path $UpdatesDir "CoreFlowStudio-$Version-full.zip"
    $PatchPackage = Join-Path $UpdatesDir "CoreFlowStudio-$ResolvedPreviousVersion-to-$Version-patch.zip"
    $Manifest = Join-Path $UpdatesDir "latest.json"
    if (-not (Test-Path $FullPackage)) {
        throw "Expected full update package was not created: $FullPackage"
    }
    if (-not (Test-Path $Manifest)) {
        throw "Expected update manifest was not created: $Manifest"
    }

    $Assets = @($FullPackage)
    if (Test-Path $PatchPackage) {
        $Assets += $PatchPackage
    }
    $Assets += $Manifest

    if (-not $NoPush) {
        $ExistingTag = (& git tag --list $Tag) | Out-String
        if ([string]::IsNullOrWhiteSpace($ExistingTag)) {
            Invoke-Checked -FilePath "git" -Arguments @("tag", "-a", $Tag, "-m", "CoreFlow Studio $Version")
        } else {
            Write-Host "Local tag $Tag already exists at HEAD; reusing it."
        }
        Invoke-Checked -FilePath "git" -Arguments @("push", $Remote, $Branch)
        Invoke-Checked -FilePath "git" -Arguments @("push", $Remote, $Tag)
    }

    $Notes = New-ReleaseNotes -Version $Version -PreviousVersion $ResolvedPreviousVersion
    $ReleaseArgs = @("release", "create", $Tag) + $Assets + @(
        "--repo",
        $Repository,
        "--title",
        "CoreFlow Studio $Version",
        "--notes",
        $Notes
    )
    Invoke-Checked -FilePath $Gh -Arguments $ReleaseArgs

    Write-Host "Published CoreFlow Studio ${Version}:"
    Write-Host "https://github.com/$Repository/releases/tag/$Tag"
}
finally {
    Pop-Location
}
