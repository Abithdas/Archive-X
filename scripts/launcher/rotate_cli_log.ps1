# ============================================================================
# FILE: scripts/launcher/rotate_cli_log.ps1
# PURPOSE: Launcher step: rotates the CLI log.
# ============================================================================
[CmdletBinding()]
param(
    [int]$MaxBytes = 10485760,
    [int]$BackupCount = 5
)
$ErrorActionPreference = 'SilentlyContinue'
$LocalAppData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($LocalAppData)) { $LocalAppData = Join-Path $HOME 'AppData\Local' }
$Log = Join-Path $LocalAppData 'Archive X\logs\launcher-cli.log'
if (-not (Test-Path -LiteralPath $Log)) { exit 0 }
try {
    $item = Get-Item -LiteralPath $Log -ErrorAction Stop
    if ($item.Length -lt $MaxBytes) { exit 0 }
    for ($i = $BackupCount; $i -ge 1; $i--) {
        $src = if ($i -eq 1) { $Log } else { "$Log.$($i-1)" }
        $dst = "$Log.$i"
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
        if (Test-Path -LiteralPath $src) { Move-Item -LiteralPath $src -Destination $dst -Force }
    }
} catch { exit 0 }
