@echo off
REM ARCHIVE-X: Windows launcher. Double-click to set up and start Archive X.
REM ARCHIVE-X: Hands off to Setup-And-Run.ps1 and the scripts in scripts\launcher.
setlocal EnableExtensions EnableDelayedExpansion
set "APP_DIR=%~dp0"
set "APP_DIR_PS=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
set "ARCHIVE_X_LAUNCH_ID=%RANDOM%%RANDOM%%RANDOM%%RANDOM:~-1%"
set "LOCALAPPDATA_FALLBACK=%LOCALAPPDATA%"
if not defined LOCALAPPDATA_FALLBACK set "LOCALAPPDATA_FALLBACK=%USERPROFILE%\AppData\Local"
if not exist "%LOCALAPPDATA_FALLBACK%\Archive X\logs" mkdir "%LOCALAPPDATA_FALLBACK%\Archive X\logs" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%scripts\launcher\rotate_cli_log.ps1" >nul 2>&1
set "ARCHIVE_X_LOG_ROTATED=1"
echo %DATE% %TIME% [bat] launch_id=%ARCHIVE_X_LAUNCH_ID% phase=start pid=%PROCESS_ID% app_dir=%APP_DIR%>>"%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"

echo.
echo Archive X
 echo Preparing startup...
echo Launch ID: %ARCHIVE_X_LAUNCH_ID%

echo Running startup preflight...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%scripts\launcher\prepare_start.ps1" -AppDir "%APP_DIR_PS%"
set "PREFLIGHT_CODE=%ERRORLEVEL%"
if "!PREFLIGHT_CODE!"=="2" (
  echo.
  echo Archive X is already running from this installation.
  echo Launch log: "%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"
  echo.
  pause
  endlocal & exit /b 2
)
if not "!PREFLIGHT_CODE!"=="0" (
  echo.
  echo Startup preflight failed.
  echo Launch log: "%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"
  echo Exit code: !PREFLIGHT_CODE!
  echo Run Setup-And-Run.ps1 for repair if needed.
  echo.
  pause
  endlocal & exit /b !PREFLIGHT_CODE!
)

if not exist ".venv\Scripts\python.exe" (
  echo Project environment is missing. Running setup/repair...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%Setup-And-Run.ps1" -SetupOnly
  set "SETUP_CODE=%ERRORLEVEL%"
  if not "!SETUP_CODE!"=="0" (
    echo.
    echo Archive X setup/repair failed.
    echo Launch log: "%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"
    echo Exit code: !SETUP_CODE!
    echo.
    pause
    endlocal & exit /b !SETUP_CODE!
  )
) else (
  echo Checking Archive X Python dependencies...
  ".venv\Scripts\python.exe" "%APP_DIR%scripts\setup\setup_dependency_check.py" --repair
  set "DEPENDENCY_CODE=%ERRORLEVEL%"
  if not "!DEPENDENCY_CODE!"=="0" (
    echo.
    echo Archive X dependency repair failed.
    echo Launch log: "%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"
    echo Exit code: !DEPENDENCY_CODE!
    echo.
    pause
    endlocal & exit /b !DEPENDENCY_CODE!
  )
)

echo.
rem v29.4.390: the optional local AI runtime/model (~2.5 GB) is checked and, if needed,
rem downloaded in a separate minimized window so it never delays or blocks starting Archive X.
echo Checking local AI prerequisites in the background (log: "%LOCALAPPDATA_FALLBACK%\Archive X\logs\ai-bootstrap.log")
start "Archive X - local AI setup" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%scripts\setup\setup_ai_prerequisites.ps1" -AppDir "%APP_DIR_PS%"

echo.
echo Starting Archive X...
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%APP_DIR%scripts\launcher\run_supervised.ps1" -AppDir "%APP_DIR_PS%" -LaunchId "%ARCHIVE_X_LAUNCH_ID%"
set "EXIT_CODE=%ERRORLEVEL%"
echo %DATE% %TIME% [bat] launch_id=%ARCHIVE_X_LAUNCH_ID% phase=shutdown exit_code=%EXIT_CODE%>>"%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"
if not "!EXIT_CODE!"=="0" (
  echo.
  echo Archive X stopped during startup or exited with an error.
  echo Launch log: "%LOCALAPPDATA_FALLBACK%\Archive X\logs\launcher-cli.log"
  echo Exit code: !EXIT_CODE!
  echo.
  pause
)
endlocal & exit /b %EXIT_CODE%
