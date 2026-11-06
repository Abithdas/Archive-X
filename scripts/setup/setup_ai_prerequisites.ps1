# ============================================================================
# FILE: scripts/setup/setup_ai_prerequisites.ps1
# PURPOSE: Downloads the optional local AI runtime and model.
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)] [string]$AppDir
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($AppDir)) { $AppDir = Join-Path $PSScriptRoot '..\..' }
# Windows PowerShell can preserve a trailing quote when a quoted path ends in a backslash
# (for example: -AppDir "E:\New folder (2)\"). Normalize the caller value before path resolution.
$AppDir = $AppDir.Trim().Trim([char]34)
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path.TrimEnd('\')

$LocalAppData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($LocalAppData)) { $LocalAppData = Join-Path $HOME 'AppData\Local' }
$BootstrapLog = Join-Path $LocalAppData 'Archive X\logs\ai-bootstrap.log'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $BootstrapLog) | Out-Null

function Write-AIBootstrapLog([string]$Message) {
    $line = "{0} [ai-bootstrap] {1}" -f ([DateTime]::UtcNow.ToString('o')), $Message
    Add-Content -LiteralPath $BootstrapLog -Value $line -Encoding UTF8
    Write-Host $Message
}

function Get-ActiveDataRoot {
    $locationFile = Join-Path $LocalAppData 'Archive X\.data_location.json'
    if (Test-Path -LiteralPath $locationFile) {
        try {
            $state = Get-Content -LiteralPath $locationFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($state.active_path) {
                return [System.IO.Path]::GetFullPath([string]$state.active_path)
            }
        } catch {
            Write-AIBootstrapLog "Could not read data-location state; using default Archive X data directory. $($_.Exception.Message)"
        }
    }
    return (Join-Path $LocalAppData 'Archive X')
}

function Invoke-CurlDownload([string]$Url, [string]$Destination) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { throw 'curl.exe is required for automatic AI prerequisite download on Windows 10/11.' }
    $dir = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Write-AIBootstrapLog "Downloading $Url"
    & $curl.Source '-L' '--fail' '--retry' '3' '--retry-all-errors' '--connect-timeout' '20' '--continue-at' '-' '--output' $Destination $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed with curl exit code $LASTEXITCODE" }
    if (-not (Test-Path -LiteralPath $Destination)) { throw "Download did not create $Destination" }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Ensure-LlamaRuntime([string]$RuntimeDir) {
    $runtimeExe = Join-Path $RuntimeDir 'llama-server.exe'
    if (Test-Path -LiteralPath $runtimeExe) {
        Write-AIBootstrapLog "llama.cpp runtime already present: $runtimeExe"
        return
    }

    # llama.cpp's bNNNN nightly releases are intentionally marked prerelease, but
    # they are the release channel that carries the pre-built Windows binaries.
    # Do not filter prerelease releases out. Instead, select the newest official
    # bNNNN release that carries the exact Windows x64 CPU asset, and require the
    # GitHub-published SHA-256 digest before accepting it.
    $headers = @{ 'User-Agent' = 'Archive-X-AI-Bootstrap' }
    $releases = Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30' -Headers $headers
    $asset = $null
    $release = $null
    $inspected = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @($releases)) {
        if ($candidate.draft) { continue }
        if ([string]$candidate.tag_name -notmatch '^b[0-9]+$') { continue }
        $inspected.Add([string]$candidate.tag_name)
        $candidateAsset = @($candidate.assets | Where-Object { $_.name -match '^llama-b[0-9]+-bin-win-cpu-x64\.zip$' -and $_.browser_download_url -and $_.digest }) | Select-Object -First 1
        if ($candidateAsset) {
            $release = $candidate
            $asset = $candidateAsset
            break
        }
    }
    if (-not $asset) { throw "Could not find the official llama.cpp Windows x64 CPU release asset with a GitHub SHA-256 digest in the recent bNNNN release set. Inspected=$($inspected -join ',')" }

    $version = [string]$release.tag_name
    $zip = Join-Path $env:TEMP "Archive-X-$version-llama-cpu.zip"
    $extract = Join-Path $env:TEMP "Archive-X-$version-llama-cpu"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }

    Write-AIBootstrapLog "Installing llama.cpp $version (official Windows x64 CPU build)."
    Invoke-CurlDownload -Url ([string]$asset.browser_download_url) -Destination $zip
    $actual = Get-Sha256 $zip
    $expected = ([string]$asset.digest).Replace('sha256:','').ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        throw "llama.cpp SHA-256 verification failed. Expected $expected, got $actual."
    }

    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $server = Get-ChildItem -LiteralPath $extract -Filter 'llama-server.exe' -Recurse -File | Select-Object -First 1
    if (-not $server) { throw 'The verified llama.cpp archive did not contain llama-server.exe.' }
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $sourceRoot = $server.Directory.FullName
    Copy-Item -Path (Join-Path $sourceRoot '*') -Destination $RuntimeDir -Recurse -Force
    if (-not (Test-Path -LiteralPath $runtimeExe)) { throw 'llama-server.exe was not installed successfully.' }
    Write-AIBootstrapLog "llama.cpp installed successfully at $runtimeExe"
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
}

function Ensure-QwenModel([string]$ModelDir) {
    $sourceFileName = 'Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
    $fileName = 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
    $target = Join-Path $ModelDir $fileName
    $partial = "$target.partial"
    $expectedSha = '2fde00ce69dd4899c70d020845e2638353015bba0fdf161b3eb965f2bca4464e'
    $revision = 'd3037fec123e6fcf1b894f42ff01bd83d4622955'
    $url = "https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/$revision/$sourceFileName"

    $marker = "$target.verified"
    function Get-ModelStamp([string]$Path) {
        $info = Get-Item -LiteralPath $Path
        return ('{0}|{1}|{2}' -f $expectedSha, $info.Length, $info.LastWriteTimeUtc.Ticks)
    }

    if (Test-Path -LiteralPath $target) {
        # Hashing a ~2.5 GB file takes many seconds, so it is done once; after
        # that the same size + modified time + recorded SHA-256 is trusted.
        try {
            if ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw).Trim() -eq (Get-ModelStamp $target))) {
                Write-AIBootstrapLog "Qwen3-4B-Instruct-2507 Q4_K_M model already present and verified (cached check)."
                return
            }
        } catch { }
        $actual = Get-Sha256 $target
        if ($actual -eq $expectedSha) {
            try { Set-Content -LiteralPath $marker -Value (Get-ModelStamp $target) -Encoding ASCII } catch { }
            Write-AIBootstrapLog "Qwen3-4B-Instruct-2507 Q4_K_M model already present and verified."
            return
        }
        Write-AIBootstrapLog 'Existing Qwen model failed SHA-256 verification; removing it before a clean download.'
        Remove-Item -LiteralPath $target -Force
    }

    Write-AIBootstrapLog 'Downloading Qwen3-4B-Instruct-2507 Q4_K_M (~2.5 GB). This is a one-time first-run download.'
    Invoke-CurlDownload -Url $url -Destination $partial
    $actual = Get-Sha256 $partial
    if ($actual -ne $expectedSha) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        throw "Qwen model SHA-256 verification failed. Expected $expectedSha, got $actual."
    }
    Move-Item -LiteralPath $partial -Destination $target -Force
    try { Set-Content -LiteralPath $marker -Value (Get-ModelStamp $target) -Encoding ASCII } catch { }
    Write-AIBootstrapLog "Qwen model installed and verified at $target"
}

$lockStream = $null
try {
    $lockPath = Join-Path $LocalAppData 'Archive X\ai-bootstrap.lock'
    $lockStream = [System.IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    Write-AIBootstrapLog 'Another local AI setup is already running; this one exits.'
    exit 0
}
try {
    $dataRoot = Get-ActiveDataRoot
    $aiRoot = Join-Path $dataRoot 'ai'
    $runtimeDir = Join-Path $aiRoot 'runtime'
    $modelDir = Join-Path $aiRoot 'models'
    New-Item -ItemType Directory -Force -Path $runtimeDir, $modelDir | Out-Null
    Write-AIBootstrapLog "AI prerequisite bootstrap started. data_root=$dataRoot"
    Ensure-LlamaRuntime -RuntimeDir $runtimeDir
    Ensure-QwenModel -ModelDir $modelDir
    Write-AIBootstrapLog 'AI prerequisite bootstrap completed successfully.'
    if ($lockStream) { $lockStream.Dispose() }
    exit 0
} catch {
    Write-AIBootstrapLog "AI prerequisite bootstrap FAILED: $($_.Exception.Message)"
    Write-Host "AI prerequisite setup failed. See $BootstrapLog"
    exit 1
}
