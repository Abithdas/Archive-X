# ============================================================================
# FILE: scripts/launcher/run_supervised.ps1
# PURPOSE: Launcher step: runs the app under supervision.
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string]$AppDir,
    [Parameter(Mandatory=$true)] [string]$LaunchId,
    [int]$PostCompleteGraceSeconds = 5,
    [int]$MaxShutdownWaitSeconds = 150
)

$ErrorActionPreference = 'Stop'
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path.TrimEnd('\')
$Python = Join-Path $AppDir '.venv\Scripts\python.exe'
$RunScript = Join-Path $AppDir 'run.py'
if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $RunScript)) {
    Write-Error "Archive X supervised launch is missing Python or run.py in $AppDir"
    exit 2
}

$LocalAppData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($LocalAppData)) { $LocalAppData = Join-Path $HOME 'AppData\Local' }
$LogPath = Join-Path $LocalAppData 'Archive X\logs\launcher-cli.log'
$TempRoot = [System.IO.Path]::GetTempPath()
$CompletionMarker = Join-Path $TempRoot "Archive-X-shutdown-$LaunchId.complete"
$RequestMarker = Join-Path $TempRoot "Archive-X-shutdown-$LaunchId.requested"

function Write-LauncherLog([string]$Message) {
    try {
        $parent = Split-Path -Parent $LogPath
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("{0} [supervisor] launch_id={1} {2}" -f [DateTime]::UtcNow.ToString('o'), $LaunchId, $Message)
    } catch { }
}

function Get-OwnedArchiveXProcess([int]$PidToCheck) {
    # Never infer ownership from process name alone. Verify PID, exact venv
    # interpreter path, and this installation's run.py command line every time.
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId = $PidToCheck" -ErrorAction Stop
        if ($null -eq $p) { return $null }
        $actualExe = [string]$p.ExecutablePath
        $actualCmd = [string]$p.CommandLine
        if ([string]::IsNullOrWhiteSpace($actualExe) -or [string]::IsNullOrWhiteSpace($actualCmd)) { return $null }
        if (-not [string]::Equals([System.IO.Path]::GetFullPath($actualExe), [System.IO.Path]::GetFullPath($Python), [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
        if ($actualCmd.IndexOf([System.IO.Path]::GetFullPath($RunScript), [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
        return $p
    } catch { return $null }
}

function Finalize-OwnedProcess([string]$Reason) {
    $child.Refresh()
    if ($child.HasExited) { return $true }
    $owned = Get-OwnedArchiveXProcess $childPid
    if ($null -ne $owned) {
        Write-LauncherLog "phase=finalize_owned_process_tree pid=$childPid reason=$Reason"
        $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        if (Test-Path -LiteralPath $taskkill) {
            & $taskkill /PID $childPid /T /F *> $null
            Start-Sleep -Milliseconds 700
            $child.Refresh()
        }
    } else {
        # CIM identity checks can fail on restricted systems. The Process handle
        # still refers to the exact child started above, so fall back to killing
        # only that handle's process (not every python.exe and not an unverified PID).
        Write-LauncherLog "phase=tree_identity_unavailable pid=$childPid reason=$Reason fallback=owned_process_handle"
    }
    if (-not $child.HasExited) {
        try { $child.Kill() } catch { }
        try { $child.WaitForExit(2500) | Out-Null } catch { }
        $child.Refresh()
    }
    if ($child.HasExited) {
        Write-LauncherLog "phase=owned_process_finalized pid=$childPid reason=$Reason"
        return $true
    }
    Write-LauncherLog "phase=finalization_not_confirmed pid=$childPid reason=$Reason"
    return $false
}

try {
    Remove-Item -LiteralPath $CompletionMarker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $RequestMarker -Force -ErrorAction SilentlyContinue
} catch { }
$env:ARCHIVE_X_LAUNCH_ID = $LaunchId
$env:ARCHIVE_X_SHUTDOWN_MARKER = $CompletionMarker
$env:ARCHIVE_X_SHUTDOWN_REQUEST_MARKER = $RequestMarker
$env:ARCHIVE_X_SHUTDOWN_SUPERVISED = '1'

Write-LauncherLog "phase=python_start executable=$Python script=$RunScript"
try {
    # NoNewWindow keeps startup logs in the batch console. This PowerShell
    # supervisor is the Python process's parent, so a process-tree cleanup
    # cannot accidentally kill the supervisor itself.
    $child = Start-Process -FilePath $Python -ArgumentList ('"{0}"' -f $RunScript) -WorkingDirectory $AppDir -NoNewWindow -PassThru
} catch {
    Write-LauncherLog "phase=python_start_failed error=$($_.Exception.Message)"
    Write-Error $_
    exit 3
}
$childPid = [int]$child.Id
Write-LauncherLog "phase=python_started pid=$childPid"
$shutdownRequestedAt = $null
$completionSeenAt = $null
$forcedFinalization = $false

while ($true) {
    $child.Refresh()
    if ($child.HasExited) { break }

    if ($null -eq $shutdownRequestedAt -and (Test-Path -LiteralPath $RequestMarker)) {
        $shutdownRequestedAt = [DateTime]::UtcNow
        Write-LauncherLog "phase=user_exit_requested pid=$childPid request_marker=$RequestMarker"
    }

    if ($null -ne $shutdownRequestedAt -and (Test-Path -LiteralPath $CompletionMarker)) {
        if ($null -eq $completionSeenAt) {
            $completionSeenAt = [DateTime]::UtcNow
            Write-LauncherLog "phase=app_cleanup_complete pid=$childPid completion_marker=$CompletionMarker"
        }
        $elapsedAfterComplete = ([DateTime]::UtcNow - $completionSeenAt).TotalSeconds
        if ($elapsedAfterComplete -ge [Math]::Max(1, $PostCompleteGraceSeconds)) {
            $forcedFinalization = Finalize-OwnedProcess 'python_still_alive_after_clean_lifespan'
            break
        }
    } elseif ($null -ne $shutdownRequestedAt) {
        $elapsedAfterRequest = ([DateTime]::UtcNow - $shutdownRequestedAt).TotalSeconds
        if ($elapsedAfterRequest -ge [Math]::Max(30, $MaxShutdownWaitSeconds)) {
            Write-LauncherLog "phase=shutdown_cleanup_timeout pid=$childPid seconds=$([int]$elapsedAfterRequest) completion_marker_missing=1 recovery_state_may_be_required=1"
            $forcedFinalization = Finalize-OwnedProcess 'graceful_shutdown_timeout'
            break
        }
    }
    Start-Sleep -Milliseconds 500
}

try { $child.WaitForExit() } catch { }
$child.Refresh()
if ($forcedFinalization) {
    # The user explicitly requested Exit and the launcher has ended the exact
    # owned Python process after its cleanup grace period; don't pause the BAT.
    exit 0
}
try {
    $code = [int]$child.ExitCode
    Write-LauncherLog "phase=python_exit pid=$childPid exit_code=$code"
    exit $code
} catch {
    Write-LauncherLog "phase=python_exit pid=$childPid exit_code=0 exit_code_read_failed"
    exit 0
}
