[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,

    [string]$Sha256Path = "",

    [ValidateRange(1, 300)]
    [int]$SelfTestTimeoutSeconds = 30
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Read-ExactBytes {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.Stream]$Stream,

        [Parameter(Mandatory = $true)]
        [byte[]]$Buffer
    )

    $offset = 0
    while ($offset -lt $Buffer.Length) {
        $read = $Stream.Read($Buffer, $offset, $Buffer.Length - $offset)
        if ($read -le 0) {
            throw "Unexpected end of executable while reading PE headers"
        }
        $offset += $read
    }
}

$resolvedExe = [System.IO.Path]::GetFullPath($ExePath)
if (!(Test-Path -LiteralPath $resolvedExe -PathType Leaf)) {
    throw "Executable does not exist: $resolvedExe"
}

$stream = [System.IO.File]::Open(
    $resolvedExe,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
try {
    if ($stream.Length -lt 90) {
        throw "Executable is too small to contain valid MZ and PE headers"
    }

    $dosHeader = New-Object byte[] 64
    Read-ExactBytes -Stream $stream -Buffer $dosHeader
    if ($dosHeader[0] -ne 0x4D -or $dosHeader[1] -ne 0x5A) {
        throw "Executable does not have an MZ header"
    }

    $peOffset = [System.BitConverter]::ToInt32($dosHeader, 0x3C)
    if ($peOffset -lt 64 -or $peOffset -gt ($stream.Length - 26)) {
        throw "Executable has an invalid PE header offset"
    }

    $stream.Position = $peOffset
    $peHeader = New-Object byte[] 26
    Read-ExactBytes -Stream $stream -Buffer $peHeader
    if (
        $peHeader[0] -ne 0x50 -or
        $peHeader[1] -ne 0x45 -or
        $peHeader[2] -ne 0x00 -or
        $peHeader[3] -ne 0x00
    ) {
        throw "Executable does not have a PE signature"
    }

    $machine = [System.BitConverter]::ToUInt16($peHeader, 4)
    if ($machine -ne 0x8664) {
        throw ("Executable is not Windows x64 (machine 0x{0:X4})" -f $machine)
    }

    $optionalHeaderMagic = [System.BitConverter]::ToUInt16($peHeader, 24)
    if ($optionalHeaderMagic -ne 0x020B) {
        throw ("Executable is not PE32+ (optional-header magic 0x{0:X4})" -f $optionalHeaderMagic)
    }
} finally {
    $stream.Dispose()
}

$signature = Get-AuthenticodeSignature -LiteralPath $resolvedExe
if ($signature.Status.ToString() -ne "NotSigned") {
    throw "Expected an unsigned executable, but Authenticode status is $($signature.Status)"
}

$selfTest = $null
try {
    $startParameters = @{
        FilePath = $resolvedExe
        ArgumentList = @("--self-test", "offline")
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $selfTest = Start-Process @startParameters

    $timeoutMilliseconds = $SelfTestTimeoutSeconds * 1000
    if (!$selfTest.WaitForExit($timeoutMilliseconds)) {
        try {
            $selfTest.Kill()
            $selfTest.WaitForExit()
        } catch {
            # Preserve the timeout as the primary failure.
        }
        throw "Offline self-test timed out after $SelfTestTimeoutSeconds seconds"
    }

    if ($selfTest.ExitCode -ne 0) {
        throw "Offline self-test failed with exit code $($selfTest.ExitCode)"
    }
} finally {
    if ($null -ne $selfTest) {
        $selfTest.Dispose()
    }
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedExe).Hash.ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($Sha256Path)) {
    $Sha256Path = "$resolvedExe.sha256"
}
$resolvedSha256 = [System.IO.Path]::GetFullPath($Sha256Path)
$sha256Directory = [System.IO.Path]::GetDirectoryName($resolvedSha256)
if (!(Test-Path -LiteralPath $sha256Directory -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $sha256Directory -Force)
}

$artifactName = [System.IO.Path]::GetFileName($resolvedExe)
$hashLine = "$hash  $artifactName$([System.Environment]::NewLine)"
[System.IO.File]::WriteAllText(
    $resolvedSha256,
    $hashLine,
    [System.Text.Encoding]::ASCII
)

[PSCustomObject]@{
    File = $artifactName
    Bytes = (Get-Item -LiteralPath $resolvedExe).Length
    Format = "PE32+"
    Machine = "AMD64"
    Signature = "NotSigned"
    SelfTest = "offline:passed"
    SHA256 = $hash
    HashFile = [System.IO.Path]::GetFileName($resolvedSha256)
} | Format-List
