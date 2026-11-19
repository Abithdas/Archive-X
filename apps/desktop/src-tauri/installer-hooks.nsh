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

; Windows keeps the icon of every shortcut and of the taskbar in its own cache and does not look again when the program inside changes: after an
; update the desktop shortcut, the Start menu entry and the pinned taskbar button kept showing the old logo. Tell Explorer that icons changed.
!macro NSIS_HOOK_POSTINSTALL
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend
