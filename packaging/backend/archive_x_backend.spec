# PyInstaller spec for the Archive X desktop backend (one-folder build).
# Build:  pyinstaller --noconfirm --distpath dist --workpath build packaging/backend/archive_x_backend.spec
# One-folder, not one-file: one-file unpacks hundreds of MB to a temp folder on every launch.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
PKG = ROOT / "telegram_archive"

# The app imports many modules lazily (inside functions, __import__) - take the whole package.
hiddenimports = collect_submodules("telegram_archive")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["websockets", "websockets.legacy", "wsproto", "h11", "h2", "python_socks", "qrcode", "PIL.Image"]

datas = [
    # Fonts and icons the React UI loads from /static.
    (str(PKG / "web" / "static"), "telegram_archive/web/static"),
    # media_manager/mm_*.py are exec()'d as source text by telegram_media_manager.py.
    (str(PKG / "media_manager"), "telegram_archive/media_manager"),
    (str(PKG / "py.typed"), "telegram_archive"),
    # Optional React frontend (served at /); present once `npm run build` has run.
] + ([(str(ROOT / "apps" / "web" / "dist"), "frontend/dist")] if (ROOT / "apps" / "web" / "dist").is_dir() else [])   # kept under the old name inside the bundle: the backend looks there
datas += collect_data_files("av") + collect_data_files("cv2") + collect_data_files("certifi")
binaries = collect_dynamic_libs("av") + collect_dynamic_libs("cv2")

# Live captions (optional): bundle the speech engine and its native runtime when it is installed in the build environment.
try:
    from PyInstaller.utils.hooks import collect_all
    for _pkg in ("faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "nudenet"):
        _d, _b, _h = collect_all(_pkg)
        datas += _d; binaries += _b; hiddenimports += _h
except Exception:
    pass

a = Analysis([str(ROOT / "packaging" / "backend" / "entry.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter", "pytest", "IPython"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="archive-x-backend", console=True,
          upx=False, icon=None)          # console=True so the shell can read the ARCHIVE_X_READY line from stdout
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="archive-x-backend")
