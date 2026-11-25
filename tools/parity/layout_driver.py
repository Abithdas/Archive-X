"""Runs inside a fresh Python process for `gen_layout_vectors.py`: imports the real `config` under the environment the case set up, runs the case's
operations, and writes what they returned (or raised) to a file. Not meant to be run by hand.

    python -I layout_driver.py <repo root> <case json file> <result file> <test root>
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
from pathlib import Path

REPO, CASE, RESULT, BASE = sys.argv[1:5]
sys.path.insert(0, REPO)


def norm(value):
    if isinstance(value, str):
        return value.replace(BASE, "{root}")
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [norm(v) for v in value]
    return value


def sub(value):
    if isinstance(value, str):
        return value.replace("{root}", BASE)
    if isinstance(value, dict):
        return {k: sub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sub(v) for v in value]
    return value


def attempt(fn):
    try:
        return {"ok": norm(fn())}
    except BaseException as exc:  # the text of what Python raises is part of what is recorded
        return {"error": [type(exc).__name__, norm(str(exc))]}


def main_functions():
    """The start-up helpers of main.py, taken from its source so that importing the whole application (which makes folders of its own) is not needed."""
    import ast
    import types
    source = (Path(REPO) / "telegram_archive" / "main.py").read_text(encoding="utf-8")
    keep = [n for n in ast.parse(source).body if (isinstance(n, ast.FunctionDef) and n.name in ("write_default_config", "remove_obsolete_files"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "OBSOLETE_FILES" for t in n.targets))]
    from telegram_archive.core import desktop_mode
    from telegram_archive.core.swallowed import note
    module = types.SimpleNamespace()
    space = {"config": config, "json": json, "desktop_mode": desktop_mode, "_swallowed_note": note, "print": print}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "main.py", "exec"), space)
    module.write_default_config = space["write_default_config"]
    module.remove_obsolete_files = space["remove_obsolete_files"]
    return module


spec = json.load(open(CASE))
out = {"ops": []}
if spec.get("frozen"):
    sys.frozen = True
started = attempt(lambda: __import__("telegram_archive.config"))
out["startup"] = {"ok": None} if "ok" in started else started
if "ok" in started:
    from telegram_archive import config

    config.APP_DIR = Path(BASE) / "app"
    config.LEGACY_DATA_DIR = config.APP_DIR / "data"
    config.LEGACY_CONFIG_PATH = config.APP_DIR / "config.json"
    for op in sub(spec.get("ops", [])):
        kind = op["op"]
        if kind == "state":
            res = attempt(config.get_data_location_state)
        elif kind == "request_move":
            res = attempt(lambda: config.request_data_move(op["target"]))
        elif kind == "cancel":
            res = attempt(config.cancel_pending_data_move)
        elif kind == "migrate":
            res = attempt(config.migrate_legacy_data)
        elif kind == "paths":
            res = attempt(lambda: {k: str(v) for k, v in sorted(vars(config).items()) if k.isupper() and not k.startswith("_") and isinstance(v, Path) and k not in ("PACKAGE_DIR", "APP_DIR")})
        elif kind == "settings":
            res = attempt(lambda: {k: ("inf" if getattr(config, k) == float("inf") else getattr(config, k)) for k in ("HOST", "PORT", "LOCAL_PLAYER_PORT", "LOCAL_PLAYER_HOST", "METADATA_CACHE_TTL_SECONDS", "TELEGRAM_BACKGROUND_CONNECT_TIMEOUT_SECONDS")})
        elif kind == "default_config":
            res = attempt(lambda: main_functions().write_default_config())
        elif kind == "obsolete":
            def run():
                main = main_functions()
                config.PACKAGE_DIR = Path(BASE) / "pkg"
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    main.remove_obsolete_files()
                return [line.split(": ", 1)[1] for line in buf.getvalue().splitlines() if line.startswith("  Removed obsolete module: ")]
            res = attempt(run)
        elif kind == "desktop":
            def run():
                from telegram_archive.core import desktop_mode as d
                secret = d.launch_secret()
                return {"landing": d.landing(), "enabled": d.enabled(), "port": d.requested_port(), "handoff": d._handoff_page(),
                        "cookie": d._cookie_value(), "matches": [d.secret_matches(c) for c in [secret, "", None, secret + "x", secret[:-1]]]}
            res = attempt(run)
        elif kind == "secrets":
            def run():
                steps = []
                for step in op["steps"]:
                    if step[0] == "set":
                        config.set_secret(step[1], step[2])
                    steps.append(config.load_secrets())
                return {"after_each": steps, "is_secret": [config.is_secret_key(k) for k in op["keys"]], "get": [config.get_secret(k, "dflt") for k in op["keys"]]}
            res = attempt(run)
        else:
            raise SystemExit(f"unknown op {kind}")
        out["ops"].append(res)
json.dump(out, open(RESULT, "w"))
