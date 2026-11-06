# PyInstaller spec for the Archive X desktop backend (one-folder build).
# Build:  pyinstaller --noconfirm --distpath dist --workpath build desktop/backend/archive_x_backend.spec
# One-folder, not one-file: one-file unpacks hundreds of MB to a temp folder on every launch.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
PKG = ROOT / "telegram_archive"

# The app imports many modules lazily (inside functions, __import__) - take the whole package.
hiddenimports = collect_submodules("telegram_archive")
hiddenimports += collect_submodules("uvicorn") + collect_submodules("hypercorn") + collect_submodules("aioquic")
hiddenimports += ["websockets", "websockets.legacy", "wsproto", "h11", "h2", "python_socks", "qrcode", "PIL.Image"]

datas = [
    # UI assets are read from disk at runtime (web/html.py composes the page from these files).
    (str(PKG / "web" / "ui"), "telegram_archive/web/ui"),
    (str(PKG / "web" / "static"), "telegram_archive/web/static"),
    # media_manager/mm_*.py are exec()'d as source text by telegram_media_manager.py.
    (str(PKG / "media_manager"), "telegram_archive/media_manager"),
    (str(PKG / "py.typed"), "telegram_archive"),
    # Optional React frontend (served at /app-next); present once `npm run build` has run.
] + ([(str(ROOT / "frontend" / "dist"), "frontend/dist")] if (ROOT / "frontend" / "dist").is_dir() else [])
datas += collect_data_files("av") + collect_data_files("cv2") + collect_data_files("certifi")
binaries = collect_dynamic_libs("av") + collect_dynamic_libs("cv2")

a = Analysis([str(ROOT / "desktop" / "backend" / "entry.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter", "pytest", "IPython"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="archive-x-backend", console=True,
          upx=False, icon=None)          # console=True so the shell can read the ARCHIVE_X_READY line from stdout
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="archive-x-backend")
