ARCHIVE X — RUN-READY PACKAGE
Version: v29.5.400

WINDOWS 10/11
1. Extract this folder to a writable location.
2. Double-click Archive-X.bat.
3. Python 3 (64-bit) is required; Python 3.12 is recommended.
The first launch installs the required Python dependencies. The optional local AI runtime/model is downloaded separately.

LINUX / macOS
Install Python 3.11 or newer, then from this folder run:
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python run.py

DATA SAFETY
This package contains application code only. It does not include your database, Telegram session, thumbnails, video cache, settings, or credentials.
