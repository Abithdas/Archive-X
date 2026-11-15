"""Entry point of the frozen Archive X backend (the Tauri shell starts this as a sidecar).

Desktop mode is forced on: the shell cannot forget to set it, and the frozen backend must never listen on the LAN
by accident. From a checkout, `python -m telegram_archive` is the equivalent.
"""
import multiprocessing
import os
import sys


def _main():
    os.environ.setdefault("ARCHIVE_X_DESKTOP", "1")
    if getattr(sys, "frozen", False):
        # Child processes spawned by the app (isolated media workers) re-run this entry point.
        multiprocessing.freeze_support()
        # Work from the bundle folder so any relative resource lookups behave like the source tree.
        os.chdir(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    from telegram_archive.main import main
    main()


if __name__ == "__main__":
    _main()
