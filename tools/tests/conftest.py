"""Four older checks are scripts (`python tools/tests/<name>.py`) that run when imported and read `sys.argv`; `pytest` must not import them.
They are run as separate programs by `test_script_checks.py`, and CI and the installer build still run them directly."""
collect_ignore = [
    "test_desktop_mode.py",
    "test_image_derivatives.py",
    "test_photo_derivatives.py",
    "test_duplicate_delete_thumbnail.py",
]
