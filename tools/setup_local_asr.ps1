[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $PSScriptRoot '..\.local\asr')
)

$ErrorActionPreference = 'Stop'
$Destination = [IO.Path]::GetFullPath($Destination)
$downloadDir = Join-Path $Destination 'downloads'
$runtimeDir = Join-Path $Destination 'runtime'
$modelDir = Join-Path $Destination 'models'
New-Item -ItemType Directory -Force -Path $downloadDir, $runtimeDir, $modelDir | Out-Null

$runtimeUrl = 'https://github.com/ggml-org/whisper.cpp/releases/download/b4938/whisper-bin-x64.zip'
$runtimeSha256 = 'c2a4b60edb11f7e11a9191ffb50929535527d4d91c9903dbe3e554583bbbc63d'
$runtimeZip = Join-Path $downloadDir 'whisper-bin-x64-b4938.zip'
$modelUrl = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en-q5_1.bin?download=true'
$modelSha256 = 'c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b'
$modelPath = Join-Path $modelDir 'ggml-tiny.en-q5_1.bin'

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-VerifiedDownload([string]$Uri, [string]$Path, [string]$ExpectedHash) {
    if ((Test-Path -LiteralPath $Path) -and (Get-Sha256 $Path) -eq $ExpectedHash) {
        Write-Host "Already verified: $Path"
        return
    }
    if (Test-Path -LiteralPath $Path) {
        $badPath = "$Path.bad-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $Path -Destination $badPath
        Write-Warning "Moved an incomplete or invalid download to $badPath"
    }
    $partialPath = "$Path.part"
    if (Test-Path -LiteralPath $partialPath) {
        Write-Host "Resuming: $Uri"
    } else {
        Write-Host "Downloading: $Uri"
    }
    & curl.exe -L --fail --retry 8 --retry-delay 2 --retry-all-errors `
        --retry-connrefused --connect-timeout 20 --continue-at - --output $partialPath $Uri
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed with curl exit code $LASTEXITCODE; rerun this script to resume"
    }
    $actual = Get-Sha256 $partialPath
    if ($actual -ne $ExpectedHash) {
        $badPartial = "$partialPath.bad-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $partialPath -Destination $badPartial
        throw "SHA256 mismatch; moved invalid download to $badPartial`nExpected: $ExpectedHash`nActual:   $actual"
    }
    Move-Item -LiteralPath $partialPath -Destination $Path -Force
    Write-Host "Verified SHA256: $actual"
}

Get-VerifiedDownload $runtimeUrl $runtimeZip $runtimeSha256
Get-VerifiedDownload $modelUrl $modelPath $modelSha256

Expand-Archive -LiteralPath $runtimeZip -DestinationPath $runtimeDir -Force
$whisperCli = Get-ChildItem -LiteralPath $runtimeDir -Recurse -Filter 'whisper-cli.exe' |
    Select-Object -First 1
if (-not $whisperCli) {
    throw "whisper-cli.exe was not found after extracting $runtimeZip"
}

$versionOutput = @(& $whisperCli.FullName --version 2>&1)
$versionLine = $versionOutput | Where-Object { $_.ToString() -match 'whisper\.cpp version:' } |
    Select-Object -First 1
$version = if ($versionLine) { $versionLine.ToString().Trim() } else { 'unknown' }
$installation = [ordered]@{
    engine = 'whisper.cpp'
    engine_version = $version
    release = 'b4938'
    executable = $whisperCli.FullName
    runtime_sha256 = $runtimeSha256
    model = $modelPath
    model_sha256 = $modelSha256
    installed_at = [DateTime]::UtcNow.ToString('o')
}
$installation | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'installation.json') -Encoding utf8

Write-Host ''
Write-Host "Local ASR is ready: $version"
Write-Host "Executable: $($whisperCli.FullName)"
Write-Host "Model:      $modelPath"
