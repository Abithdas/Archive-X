; Installer steps added to the generated NSIS installer (tauri.conf.json: bundle.windows.nsis.installerHooks).
;
; The app's helper processes (the Python backend and the front door) can still be running, or still be shutting down and saving jobs, when the
; installer starts, for example when an update is installed right after the app was closed. They keep their DLLs open, and the installer then stops
; with "Error opening file for writing: ...\backend\_internal\vcruntime140.dll". Stop them first, only the ones started from this app's own names.

!macro ArchiveXStopHelpers
  nsExec::Exec 'taskkill /F /T /IM archive-x-backend.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM ax-server.exe'
  Pop $0
  ; Windows needs a moment to let go of the files of a process that has just ended.
  Sleep 2500
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro ArchiveXStopHelpers
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro ArchiveXStopHelpers
!macroend
