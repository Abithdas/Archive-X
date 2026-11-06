"""Archive X dependency contract and optional repair helper."""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements.txt"

DEPENDENCIES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "telethon": "telethon",
    "aiohttp": "aiohttp",
    "pillow": "PIL.Image",
    "cryptography": "cryptography",
    "argon2": "argon2",
    # QR login is a supported sign-in path, not an optional cosmetic add-on.
    # Track it here so the launcher repairs environments where qrcode is absent.
    "qrcode": "qrcode",
}
PACKAGE_SPECS = {
    "fastapi": "fastapi>=0.115,<1",
    "uvicorn": "uvicorn>=0.30,<1",
    "telethon": "telethon>=1.36,<2",
    "aiohttp": "aiohttp>=3.9,<4",
    "pillow": "Pillow>=10,<13",
    "cryptography": "cryptography>=42,<47",
    "argon2": "argon2-cffi>=23.1,<26",
    "qrcode": "qrcode[pil]>=7.4,<9",
}

OPTIONAL = {
    "av": "av==14.2.0",
    "cryptg": "cryptg>=0.4",
    "hypercorn": "hypercorn[h3]>=0.18,<1",
}


def _import_name(name: str) -> str:
    return "PIL.Image" if name == "pillow" else name


def check() -> dict[str, object]:
    result: dict[str, object] = {"python": sys.executable, "required": {}, "optional": {}}
    for name, module in DEPENDENCIES.items():
        spec = PACKAGE_SPECS[name]
        try:
            if name == "argon2":
                import argon2  # explicit backup/authentication dependency contract
                mod = argon2
            elif name == "qrcode":
                # Validate the specific SVG renderer used by Telegram QR login,
                # not just the top-level package import.
                mod = importlib.import_module(module)
                importlib.import_module("qrcode.image.svg")
            else:
                mod = importlib.import_module(module)
            result["required"][name] = {"ok": True, "version": str(getattr(mod, "__version__", ""))}
        except BaseException as exc:
            result["required"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "spec": spec}
    for name, spec in OPTIONAL.items():
        try:
            mod = importlib.import_module(name)
            result["optional"][name] = {"ok": True, "version": str(getattr(mod, "__version__", ""))}
        except BaseException as exc:
            result["optional"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "spec": spec}
    result["ok"] = all(item["ok"] for item in result["required"].values())
    return result


def _requirements_without_pyav() -> list[str]:
    lines: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # PyAV is a native optional media backend. It is installed separately
        # from an official binary wheel so a missing wheel can never make the
        # entire application dependency repair fail (for example by invoking
        # a local MSVC source build).
        if line.lower().startswith("av==") or line.lower().startswith("av>=") or line.lower().startswith("av<="):
            continue
        lines.append(line)
    return lines


def repair() -> int:
    if not REQUIREMENTS.exists():
        print(f"Missing requirements file: {REQUIREMENTS}", file=sys.stderr)
        return 2

    core_specs = _requirements_without_pyav()
    core_cmd = [sys.executable, "-m", "pip", "install", "--prefer-binary", *core_specs]
    print("Installing/repairing Archive X core Python dependencies with the active interpreter:")
    print(" ".join(core_cmd))
    core_result = subprocess.run(core_cmd, check=False)
    if core_result.returncode:
        return core_result.returncode

    # Never allow pip to fall back to an MSVC source build for PyAV. Official
    # PyPI wheels exist for CPython 3.12 Windows x64 (the supported Archive X
    # Windows target). If a user's package index does not expose that wheel,
    # leave PyAV optional and let the application use its FFmpeg fallback.
    av_cmd = [sys.executable, "-m", "pip", "install", "--only-binary=:all:", OPTIONAL["av"]]
    print("Installing optional PyAV from a binary wheel only:")
    print(" ".join(av_cmd))
    av_result = subprocess.run(av_cmd, check=False)
    if av_result.returncode:
        print("WARNING: PyAV binary wheel could not be installed; continuing with the FFmpeg media fallback.", file=sys.stderr)
    return 0


def _marker_path():
    return Path(sys.prefix) / ".archive-x-deps-ok"


def _marker_value() -> str:
    """Identify the dependency contract: this file, requirements.txt and Python."""
    import hashlib
    h = hashlib.sha256()
    root = Path(__file__).resolve().parents[2]
    for name in (root / "requirements.txt", Path(__file__).resolve()):
        try:
            h.update(name.read_bytes())
        except OSError:
            h.update(b"-")
    h.update(sys.version.encode("utf-8"))
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    # Importing every dependency on every launch costs seconds. Once a check has
    # passed, skip it until requirements.txt, this script or Python changes.
    if args.repair and not args.json:
        try:
            if _marker_path().read_text(encoding="utf-8").strip() == _marker_value():
                print("Archive X Python dependencies unchanged since the last successful check.")
                return 0
        except OSError:
            pass
    result = check()
    if args.repair and not result["ok"]:
        code = repair()
        if code:
            return code
        result = check()
    if result["required"].get("argon2", {}).get("ok"):
        print("Argon2 OK")
    if not result["optional"].get("av", {}).get("ok"):
        print("WARNING: PyAV is optional and unavailable; FFmpeg fallback will be used where supported.")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Python: {result['python']}")
        for group in ("required", "optional"):
            for name, item in result[group].items():
                print(f"{name:<14} {'OK' if item['ok'] else 'MISSING'}")
        print("Required dependencies: " + ("OK" if result["ok"] else "NOT READY"))
    if result["ok"] and args.repair:
        try:
            _marker_path().write_text(_marker_value(), encoding="utf-8")
        except OSError:
            pass
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
