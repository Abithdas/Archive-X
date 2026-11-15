"""`python -m telegram_archive` starts the backend from a checkout (development and tests). The installed app runs the frozen backend
built from packaging/backend/entry.py instead."""
from .main import main

if __name__ == "__main__":
    main()
