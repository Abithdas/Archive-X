# ============================================================================
# FILE: scripts/launcher/prepare_start.ps1
# PURPOSE: Launcher step: prepares the environment before start.
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)] [string]$AppDir,
    [switch]$AllowRunning
)

$ErrorActionPreference = 'Stop'

$LocalAppData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($LocalAppData)) { $LocalAppData = Join-Path $HOME 'AppData\Local' }
$LaunchId = if ($env:ARCHIVE_X_LAUNCH_ID) { $env:ARCHIVE_X_LAUNCH_ID } else { [guid]::NewGuid().ToString("N").Substring(0,16); $env:ARCHIVE_X_LAUNCH_ID = $LaunchId }
$CliLog = Join-Path $LocalAppData 'Archive X\logs\launcher-cli.log'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CliLog) | Out-Null
$rotateLog = Join-Path $PSScriptRoot $(if ($MyInvocation.MyCommand.Path -like "*\scripts\launcher\*") { "rotate_cli_log.ps1" } else { "scripts\launcher\rotate_cli_log.ps1" })
if ((Test-Path -LiteralPath $rotateLog) -and -not $env:ARCHIVE_X_LOG_ROTATED) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $rotateLog }

function Write-PreflightLog([string]$Message) {
    $line = "{0} [preflight] launch_id=$LaunchId {1}" -f ([DateTime]::UtcNow.ToString('o')), $Message
    Add-Content -LiteralPath $CliLog -Value $line -Encoding UTF8
}

if ([string]::IsNullOrWhiteSpace($AppDir)) {
    $AppDir = Join-Path $PSScriptRoot '..\..'
}
$AppDir = $AppDir.Trim().Trim('\"')
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path.TrimEnd('\')
$RunScript = (Join-Path $AppDir 'run.py')
$ExpectedPython = (Join-Path $AppDir '.venv\Scripts\python.exe')

# Process inspection is intentionally limited to Win32_Process command lines
# that point at this installation's run.py. Unrelated Python processes are
# never touched.
$matches = @(
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = [string]$_.CommandLine
            if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }
            $cmd.IndexOf($RunScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        }
)

if ($matches.Count -eq 0) { Write-PreflightLog 'status=no_existing_process'; exit 0 }

$expected = [System.IO.Path]::GetFullPath($ExpectedPython)
$stale = @()
$healthy = @()
foreach ($process in $matches) {
    $exe = [string]$process.ExecutablePath
    if ($exe -and [System.IO.Path]::GetFullPath($exe) -ieq $expected) {
        $healthy += $process
    } else {
        $stale += $process
    }
}

if ($healthy.Count -gt 0 -and -not $AllowRunning) {
    Write-PreflightLog ('status=already_running healthy_pids=' + (($healthy | ForEach-Object {$_.ProcessId}) -join ','))
    Write-Host 'Archive X is already running from this installation; no second instance will be started.'
    Write-Host 'This preflight does not terminate unrelated Python processes.'
    exit 2
}

foreach ($process in $stale) {
    Write-PreflightLog ("phase=stale_process_stop pid={0} executable={1}" -f $process.ProcessId, $process.ExecutablePath)
    Write-Host ("Stopping stale same-installation Archive X process PID {0} using {1}." -f $process.ProcessId, $process.ExecutablePath)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
}
Write-PreflightLog 'phase=process_preflight status=success'
exit 0
