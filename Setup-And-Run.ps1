# ============================================================================
# FILE: Setup-And-Run.ps1
# PURPOSE: First-run setup and launch, called by Archive-X.bat.
# ============================================================================
[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$EnsureLan,
    [switch]$SkipRepair
)

$ErrorActionPreference = 'Stop'
$AppDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$LocalAppData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($LocalAppData)) { $LocalAppData = Join-Path $HOME 'AppData\Local' }
$LaunchId = if ($env:ARCHIVE_X_LAUNCH_ID) { $env:ARCHIVE_X_LAUNCH_ID } else { [guid]::NewGuid().ToString("N").Substring(0,16); $env:ARCHIVE_X_LAUNCH_ID = $LaunchId }
$CliLog = Join-Path $LocalAppData 'Archive X\logs\launcher-cli.log'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CliLog) | Out-Null
$rotateLog = Join-Path $PSScriptRoot $(if ($MyInvocation.MyCommand.Path -like "*\scripts\launcher\*") { "rotate_cli_log.ps1" } else { "scripts\launcher\rotate_cli_log.ps1" })
if (Test-Path -LiteralPath $rotateLog) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $rotateLog }

function Write-LauncherLog([string]$Message) {
    $line = "{0} [setup] launch_id=$LaunchId {1}" -f ([DateTime]::UtcNow.ToString('o')), $Message
    Add-Content -LiteralPath $CliLog -Value $line -Encoding UTF8
    Write-Host $Message
}

Write-LauncherLog "phase=start AppDir=$AppDir pid=$PID parent_pid=$([System.Diagnostics.Process]::GetCurrentProcess().Parent.Id)"

$preflight = Join-Path $AppDir 'scripts\launcher\prepare_start.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $preflight -AppDir $AppDir
if ($LASTEXITCODE -eq 2) { Write-LauncherLog 'Archive X is already running.'; exit 2 }
if ($LASTEXITCODE -ne 0) { Write-LauncherLog 'Startup preflight failed.'; exit $LASTEXITCODE }

$venv = Join-Path $AppDir '.venv'
$pythonExe = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-LauncherLog 'phase=venv_create Creating project virtual environment.'
    & py -3 -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        Write-LauncherLog 'Python launcher could not create the project virtual environment.'
        exit $LASTEXITCODE
    }
}

$checker = Join-Path $AppDir 'scripts\setup\setup_dependency_check.py'
if ($SkipRepair) {
    & $pythonExe $checker
} else {
    & $pythonExe $checker --repair
}
if ($LASTEXITCODE -ne 0) {
    Write-LauncherLog 'Python dependency contract is not satisfied.'
    exit $LASTEXITCODE
}

if ($EnsureLan) {
    $lan = Join-Path $AppDir 'Ensure-Archive-X-LAN.ps1'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $lan -Action Ensure
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($SetupOnly) {
    Write-LauncherLog 'Setup completed successfully.'
    exit 0
}

$aiBootstrap = Join-Path $AppDir 'scripts\setup\setup_ai_prerequisites.ps1'
if (Test-Path -LiteralPath $aiBootstrap) {
    # Optional local AI: check/download in a separate minimized window; never block startup.
    Start-Process -FilePath 'powershell.exe' -WindowStyle Minimized -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $aiBootstrap),'-AppDir',('"{0}"' -f $AppDir)) | Out-Null
}

Write-LauncherLog 'phase=application_start Starting Archive X with the project interpreter.'
& $pythonExe (Join-Path $AppDir 'run.py')
$code = $LASTEXITCODE
Write-LauncherLog "phase=shutdown Archive X exited with code $code."
exit $code
