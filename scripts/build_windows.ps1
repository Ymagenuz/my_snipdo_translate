[CmdletBinding()]
param(
    [ValidateRange(1, 300)]
    [int]$SelfTestTimeoutSeconds = 30
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$rootPrefix = $root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$python = Join-Path $root ".venv\Scripts\python.exe"
$spec = Join-Path $root "SnipDoTranslate.spec"
$checkArtifact = Join-Path $root "scripts\check_artifact.ps1"

$buildRoot = Join-Path $root "build\pyinstaller"
$onedirWork = Join-Path $buildRoot "onedir"
$onefileWork = Join-Path $buildRoot "onefile"
$distRoot = Join-Path $root "dist"
$onedirDist = Join-Path $distRoot "preflight-onedir"

function Assert-GeneratedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $full = [System.IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing generated-path operation outside the worktree: $full"
    }
}

function Remove-GeneratedTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Assert-GeneratedPath -Path $Path
    if (!(Test-Path -LiteralPath $Path)) {
        return
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to recursively remove a generated path that is a reparse point"
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The worktree virtual-environment Python was not found: $python"
}
if (!(Test-Path -LiteralPath $spec -PathType Leaf)) {
    throw "PyInstaller spec was not found: $spec"
}
if (!(Test-Path -LiteralPath $checkArtifact -PathType Leaf)) {
    throw "Artifact checker was not found: $checkArtifact"
}
if (![System.Environment]::Is64BitProcess) {
    throw "Run this build from 64-bit PowerShell so PyInstaller produces a Windows x64 executable"
}

Push-Location $root
$previousBuildMode = [System.Environment]::GetEnvironmentVariable("SNIPDO_BUILD_MODE", "Process")
try {
    # Source tests are intentionally the first executable build gate. They are offline.
    Invoke-NativeCommand -FilePath $python -Arguments @("-m", "pytest", "-q")
    Invoke-NativeCommand -FilePath $python -Arguments @(
        "-c",
        "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 'The .venv Python must be x64')"
    )
    Invoke-NativeCommand -FilePath $python -Arguments @("scripts\create_icon.py", "--check")

    foreach ($generatedPath in @($buildRoot, $distRoot)) {
        Remove-GeneratedTree -Path $generatedPath
    }

    [void](New-Item -ItemType Directory -Path $onedirWork -Force)
    [void](New-Item -ItemType Directory -Path $onefileWork -Force)
    [void](New-Item -ItemType Directory -Path $onedirDist -Force)

    $env:SNIPDO_BUILD_MODE = "onedir"
    Invoke-NativeCommand -FilePath $python -Arguments @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath", $onedirWork,
        "--distpath", $onedirDist,
        $spec
    )

    $onedirExe = Join-Path $onedirDist "SnipDoTranslate\SnipDoTranslate.exe"
    & $checkArtifact -ExePath $onedirExe -SelfTestTimeoutSeconds $SelfTestTimeoutSeconds

    $env:SNIPDO_BUILD_MODE = "onefile"
    Invoke-NativeCommand -FilePath $python -Arguments @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath", $onefileWork,
        "--distpath", $distRoot,
        $spec
    )

    $onefileExe = Join-Path $distRoot "SnipDoTranslate.exe"
    $onefileSha256 = Join-Path $distRoot "SnipDoTranslate.exe.sha256"
    & $checkArtifact -ExePath $onefileExe -Sha256Path $onefileSha256 -SelfTestTimeoutSeconds $SelfTestTimeoutSeconds

    Write-Host "Windows one-file artifact verified: $onefileExe"
    Write-Host "SHA-256 manifest: $onefileSha256"
} finally {
    if ($null -eq $previousBuildMode) {
        Remove-Item Env:SNIPDO_BUILD_MODE -ErrorAction SilentlyContinue
    } else {
        $env:SNIPDO_BUILD_MODE = $previousBuildMode
    }
    Pop-Location
}
