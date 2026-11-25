"""Record what Python's `config.py` and `main.py` do to the data folder, case by case, for `crates/ax-config/tests/layout.rs`.

    python tools/parity/gen_layout_vectors.py          # rewrite crates/ax-config/tests/layout_cases.json
    python tools/parity/gen_layout_vectors.py --check  # fail if the file is out of date

Importing `config` is itself the start-up (it picks the data folder, finishes a queued move, applies a staged restore, creates the folders, writes the
manifest), so every case runs in a fresh Python process: the files, environment and working folder are set up first, `config` is imported, then the
case's operations run. The record is what the import and each operation returned or raised, and the whole file tree afterwards (names and contents,
with the temporary folder, clock times and the restore folder's time stamp replaced). `ax-config` builds the same tree from the same case and must
produce the same answers and the same files.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "crates" / "ax-config" / "tests" / "layout_cases.json"
DRIVER = Path(__file__).with_name("layout_driver.py")

LOC = "home/.config/Archive X/.data_location.json"
DEFAULT_DATA = "home/.local/share/Archive X"
TIME_FIELDS = re.compile(r'"(generated_at|updated_at|started_at)": "[^"]*"')
RESTORE_STAMP = re.compile(r"PreRestore \d{8}_\d{6}")


def snapshot(base: Path) -> list:
    """Every file, folder and link under `base`: [path, kind, content]. Text is shown with the folder name and times replaced."""
    rows = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(dirpath)
        for name in dirnames + filenames:
            path = here / name
            rel = RESTORE_STAMP.sub("PreRestore <ts>", path.relative_to(base).as_posix())
            if path.is_symlink():
                rows.append([rel, "link", os.readlink(path).replace(str(base), "{root}")])
            elif path.is_dir():
                rows.append([rel, "dir", ""])
            else:
                raw = path.read_bytes()
                if raw.startswith(b"ARCHIVE-X-AESGCM-1\n"):
                    rows.append([rel, "file", "sealed"])  # random nonce: the bytes differ every time
                    continue
                try:
                    text = TIME_FIELDS.sub(r'"\1": "<time>"', raw.decode("utf-8").replace(str(base), "{root}"))
                    content = text if len(text.encode()) <= 300 else "sha256:" + hashlib.sha256(text.encode()).hexdigest()
                except UnicodeDecodeError:
                    content = "hex:" + raw.hex() if len(raw) <= 64 else "sha256:" + hashlib.sha256(raw).hexdigest()
                rows.append([rel, "file", content])
    return sorted(rows)


def build(base: Path, files: dict):
    for rel, what in files.items():
        path = base / rel
        what = json.loads(json.dumps(what).replace("{root}", str(base).replace("\\", "\\\\")))
        if isinstance(what, dict) and "symlink" in what:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(what["symlink"])
        elif isinstance(what, dict) and what.get("dir"):
            path.mkdir(parents=True, exist_ok=True)
        elif isinstance(what, dict) and "hex" in what:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes.fromhex(what["hex"]))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(what, encoding="utf-8")


def run_case(case: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="axlay-") as tmp:
        base = Path(tmp).resolve()
        (base / "home").mkdir()
        (base / "cwd").mkdir()
        build(base, case.get("files", {}))
        spec = base.parent / (base.name + ".case.json")
        result = base.parent / (base.name + ".result.json")
        spec.write_text(json.dumps(case))
        env = {"HOME": str(base / "home"), "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
        env.update({k: v.replace("{root}", str(base)) for k, v in case.get("env", {}).items()})
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(ROOT), str(spec), str(result), str(base)], cwd=base / "cwd", env=env,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise SystemExit(f"case {case['name']!r} crashed:\n{proc.stderr}")
        out = json.loads(result.read_text())
        spec.unlink()
        result.unlink()
        return {**case, "expect": out, "tree": snapshot(base)}


def case(name, env=None, files=None, ops=None, frozen=False):
    c = {"name": name, "env": env or {}, "files": files or {}, "ops": ops or []}
    if frozen:
        c["frozen"] = True
    return c


def loc(**state):
    return {LOC: json.dumps({"version": 1, **state})}


def tree_of(prefix, **names):
    """A small data folder: a database, some assets, an empty folder and a nested file."""
    return {f"{prefix}/database/archive.db": "db-bytes", f"{prefix}/assets/scenes/a.jpg": "jpeg", f"{prefix}/state/empty": {"dir": True},
            f"{prefix}/logs/app.log": "line\n", f"{prefix}/deep/er/still/file.txt": "x" * 3000, f"{prefix}/settings.json": '{"theme": "dark"}\n', **names}


PATHS = {"op": "paths"}
STATE = {"op": "state"}
DATA = {"ARCHIVE_X_DATA_DIR": "{root}/data"}


def cases() -> list:
    c = []
    # ---- where the data folder is
    c.append(case("default layout", ops=[PATHS, STATE]))
    c.append(case("data dir from ARCHIVE_X_DATA_DIR", DATA, ops=[PATHS, STATE]))
    c.append(case("data dir padded with spaces", {"ARCHIVE_X_DATA_DIR": "  {root}/pad  "}, ops=[PATHS]))
    c.append(case("data dir from the old variable", {"TELEGRAM_ARCHIVE_DATA_DIR": "{root}/old-name"}, ops=[PATHS, STATE]))
    c.append(case("blank new variable falls back to the old one", {"ARCHIVE_X_DATA_DIR": "   ", "TELEGRAM_ARCHIVE_DATA_DIR": "{root}/old-name"}, ops=[PATHS]))
    c.append(case("new variable wins over the old one", {"ARCHIVE_X_DATA_DIR": "{root}/new", "TELEGRAM_ARCHIVE_DATA_DIR": "{root}/old"}, ops=[PATHS]))
    c.append(case("data dir with a tilde", {"ARCHIVE_X_DATA_DIR": "~/tilde-data"}, ops=[PATHS]))
    c.append(case("data dir relative to the working folder", {"ARCHIVE_X_DATA_DIR": "rel/data"}, ops=[PATHS]))
    c.append(case("data dir with dots", {"ARCHIVE_X_DATA_DIR": "{root}/a/../b/./data"}, ops=[PATHS]))
    c.append(case("data dir through a symlink", {"ARCHIVE_X_DATA_DIR": "{root}/link/data"}, {"real": {"dir": True}, "link": {"symlink": "{root}/real"}}, ops=[PATHS]))
    c.append(case("data dir with spaces and non-ASCII", {"ARCHIVE_X_DATA_DIR": "{root}/Données é 日本/Archive X"}, ops=[PATHS]))
    c.append(case("XDG_DATA_HOME", {"XDG_DATA_HOME": "{root}/xdg-data"}, ops=[PATHS]))
    c.append(case("empty XDG_DATA_HOME", {"XDG_DATA_HOME": ""}, ops=[PATHS]))
    c.append(case("XDG_CONFIG_HOME moves the location file", {"XDG_CONFIG_HOME": "{root}/xdg-config"}, ops=[PATHS, STATE]))
    # ---- the location file
    c.append(case("location file names the active folder", files=loc(active_path="{root}/active"), ops=[PATHS, STATE]))
    c.append(case("location file that is not JSON", files={LOC: "{nope"}, ops=[PATHS, STATE]))
    c.append(case("location file that is a list", files={LOC: "[1, 2]"}, ops=[PATHS, STATE]))
    c.append(case("location file with an empty active path", files=loc(active_path="", pending_path=""), ops=[PATHS, STATE]))
    c.append(case("location file with a number as the active path", files=loc(active_path=123), ops=[PATHS, STATE]))
    c.append(case("location file with true as the active path", files=loc(active_path=True), ops=[PATHS, STATE]))
    c.append(case("location file with falsy values as the paths", files=loc(active_path=0, pending_path=[]), ops=[PATHS, STATE]))
    c.append(case("location file with a number as the queued path", files=loc(active_path="{root}/old", pending_path=7) | tree_of("old"), ops=[PATHS, STATE]))
    c.append(case("location file with a tilde", files=loc(active_path="~/tilde-active"), ops=[PATHS, STATE]))
    c.append(case("environment beats the location file and a queued move", DATA, loc(active_path="{root}/active", pending_path="{root}/queued") | tree_of("active"), ops=[PATHS, STATE]))
    # ---- a queued move of the data folder
    c.append(case("queued move", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old"), ops=[PATHS, STATE]))
    c.append(case("queued move into an existing empty folder", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old") | {"new": {"dir": True}}, ops=[PATHS]))
    c.append(case("queued move to a new nested path", files=loc(active_path="{root}/old", pending_path="{root}/x/y/z/new") | tree_of("old"), ops=[PATHS]))
    c.append(case("queued move to the same folder", files=loc(active_path="{root}/old", pending_path="{root}/old") | tree_of("old"), ops=[STATE]))
    c.append(case("queued move into itself", files=loc(active_path="{root}/old", pending_path="{root}/old/inside") | tree_of("old"), ops=[STATE]))
    c.append(case("queued move from a folder that is not there", files=loc(active_path="{root}/gone", pending_path="{root}/new"), ops=[STATE]))
    c.append(case("queued move onto an unrelated folder", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old") | {"new/photo.jpg": "mine"}, ops=[STATE]))
    c.append(case("queued move resuming into a folder that holds a database", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {"new/database/archive.db": "half"}, ops=[PATHS]))
    c.append(case("queued move resuming into a folder that holds a manifest", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {"old/PERSISTENT_DATA_MANIFEST.json": '{"manifest_version": 1}', "new/PERSISTENT_DATA_MANIFEST.json": "{}"}, ops=[PATHS]))
    marker = ".archive_x_move_in_progress.json"
    c.append(case("queued move resuming from its own marker", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {f"new/{marker}": json.dumps({"source": "{root}/old"}), "new/settings.json": "partial"}, ops=[PATHS]))
    c.append(case("queued move resuming over a folder with extra files fails its check", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {"new/database/archive.db": "half", "new/assets/leftover.bin": "keep me"}, ops=[STATE]))
    c.append(case("queued move resuming from its own marker with an extra file fails its check", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {f"new/{marker}": json.dumps({"source": "{root}/old"}), "new/partial.bin": "p"}, ops=[STATE]))
    c.append(case("queued move with a marker from another source", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {f"new/{marker}": json.dumps({"source": "{root}/other"}), "new/partial.bin": "p"}, ops=[STATE]))
    c.append(case("queued move with a broken marker", files=loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old")
                  | {f"new/{marker}": "{nope", "new/partial.bin": "p"}, ops=[STATE]))
    c.append(case("queued move ignored when the environment names the folder", DATA, loc(active_path="{root}/old", pending_path="{root}/new") | tree_of("old"), ops=[STATE]))
    c.append(case("queued move to a tilde path", files=loc(active_path="{root}/old", pending_path="~/moved") | tree_of("old"), ops=[PATHS, STATE]))
    # ---- a staged restore
    restore_marker = {"staging_path": "{root}/parent/stage"}
    stage = {"parent/stage/database/archive.db": "restored-db", "parent/stage/settings.json": '{"theme": "light"}\n', "parent/stage/extra/file.txt": "x"}
    c.append(case("staged restore", files=loc(active_path="{root}/parent/live") | tree_of("parent/live") | stage | {"parent/.archive_x_pending_restore.json": json.dumps(restore_marker)}, ops=[PATHS]))
    c.append(case("staged restore into the default folder",
                  files=tree_of(DEFAULT_DATA) | {"stage-here/database/archive.db": "r", "home/.local/share/.archive_x_pending_restore.json": json.dumps({"staging_path": "{root}/stage-here"})}, ops=[PATHS]))
    c.append(case("staged restore when the live folder is gone", files=loc(active_path="{root}/parent/live") | stage | {"parent/.archive_x_pending_restore.json": json.dumps(restore_marker)}, ops=[PATHS]))
    c.append(case("staged restore with the staging folder missing", files=loc(active_path="{root}/parent/live") | tree_of("parent/live") | {"parent/.archive_x_pending_restore.json": json.dumps(restore_marker)}, ops=[PATHS]))
    c.append(case("staged restore pointing at a file", files=loc(active_path="{root}/parent/live") | tree_of("parent/live") | {"parent/stage": "not a folder", "parent/.archive_x_pending_restore.json": json.dumps(restore_marker)}, ops=[PATHS]))
    c.append(case("staged restore ignored when the environment names the folder", {"ARCHIVE_X_DATA_DIR": "{root}/parent/live"}, tree_of("parent/live") | stage | {"parent/.archive_x_pending_restore.json": json.dumps(restore_marker)}, ops=[PATHS]))
    c.append(case("queued move, then staged restore into the new folder",
                  files=loc(active_path="{root}/old", pending_path="{root}/par/new") | tree_of("old") | {"par/stage/database/archive.db": "restored", "par/.archive_x_pending_restore.json": json.dumps({"staging_path": "{root}/par/stage"})}, ops=[PATHS]))
    # ---- the folders and the manifest
    c.append(case("an old manifest is rewritten", DATA, {"data/PERSISTENT_DATA_MANIFEST.json": "{\"manifest_version\": 0}"}, ops=[PATHS]))
    c.append(case("existing files are left alone", DATA, tree_of("data") | {"data/auth/credentials.json": "sealed", "data/assets/thumbnails/playback/t.jpg": "t"}, ops=[PATHS]))
    c.append(case("a file where a folder belongs", DATA, {"data/assets": "i am a file"}, ops=[PATHS]))
    # ---- settings read from the environment
    for label, env in [("defaults", {}), ("host and ports", {"TELEGRAM_ARCHIVE_HOST": "127.0.0.1", "TELEGRAM_ARCHIVE_PORT": " 9443 ", "TELEGRAM_ARCHIVE_PLAYER_PORT": "8_445", "TELEGRAM_ARCHIVE_PLAYER_HOST": "::1"}),
                       ("signed port", {"TELEGRAM_ARCHIVE_PORT": "+5", "TELEGRAM_ARCHIVE_PLAYER_PORT": "-1"}), ("empty port", {"TELEGRAM_ARCHIVE_PORT": ""}),
                       ("bad port", {"TELEGRAM_ARCHIVE_PORT": "abc"}), ("hex port", {"TELEGRAM_ARCHIVE_PLAYER_PORT": "0x10"}), ("decimal port", {"TELEGRAM_ARCHIVE_PORT": "80.5"}),
                       ("double underscore port", {"TELEGRAM_ARCHIVE_PORT": "8__0"}), ("underscore at the end", {"TELEGRAM_ARCHIVE_PORT": "80_"}),
                       ("cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": "3600"}), ("negative cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": "-5"}),
                       ("exponent cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": "1e3"}), ("underscore cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": "1_0.5"}),
                       ("infinite cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": "inf"}), ("nan cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": " NaN "}),
                       ("bad cache lifetime", {"ARCHIVE_X_METADATA_CACHE_TTL_SECONDS": "soon"}), ("short connect timeout", {"ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS": "1"}),
                       ("long connect timeout", {"ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS": " 7.5 "}), ("nan connect timeout", {"ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS": "nan"}),
                       ("bad connect timeout", {"ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS": "1,5"}), ("leading dot timeout", {"ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS": ".5e2"}),
                       ("quoted-looking port", {"TELEGRAM_ARCHIVE_PORT": "it's"}), ("port with separator controls", {"TELEGRAM_ARCHIVE_PORT": "\u001c80"}),
                       ("timeout with separator controls", {"ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS": "9\u001f"}), ("port with no-break spaces", {"TELEGRAM_ARCHIVE_PORT": "\u00a0\u200a81\u3000"}),
                       ("port with a non-printing character", {"TELEGRAM_ARCHIVE_PORT": "8\u200b0"}), ("port with a control character", {"TELEGRAM_ARCHIVE_PORT": "8\u00010"}),
                       ("port with a delete character", {"TELEGRAM_ARCHIVE_PORT": "8\u007f0"}), ("port with a no-break space inside", {"TELEGRAM_ARCHIVE_PORT": "8\u00a00"}),
                       ("port with an astral character", {"TELEGRAM_ARCHIVE_PORT": "8\U0001F600"}), ("port with an unassigned character", {"TELEGRAM_ARCHIVE_PORT": "8\u0378"})]:
        c.append(case(f"settings: {label}", DATA | env, ops=[{"op": "settings"}]))
    # ---- asking for a move from Settings
    old_tree = tree_of("data")
    for label, target, extra in [
        ("a new folder", "{root}/dest", {}), ("a nested new folder", "{root}/x/y/dest", {}), ("an empty folder", "{root}/dest", {"dest": {"dir": True}}),
        ("the current folder", "{root}/data", {}), ("a folder inside the current one", "{root}/data/inside", {}), ("the application folder", "{root}/app/place", {"app/readme": "x"}),
        ("a folder with other files", "{root}/dest", {"dest/mine.txt": "x"}), ("an earlier archive folder (database)", "{root}/dest", {"dest/database/archive.db": "d"}),
        ("an earlier archive folder (manifest)", "{root}/dest", {"dest/PERSISTENT_DATA_MANIFEST.json": "{}"}), ("a relative path", "rel/dest", {}), ("a tilde path", "~/dest", {}),
        ("nothing", "", {}), ("only spaces", "   ", {}), ("a path with dots", "{root}/a/../dest/.", {}), ("a path through a symlink", "{root}/link/dest", {"real": {"dir": True}, "link": {"symlink": "{root}/real"}}),
    ]:
        c.append(case(f"request a move to {label}", DATA, old_tree | extra, ops=[{"op": "request_move", "target": target}, STATE, {"op": "cancel"}, STATE]))
    c.append(case("request a move, then ask again for another", DATA, old_tree, ops=[{"op": "request_move", "target": "{root}/one"}, {"op": "request_move", "target": "{root}/two"}, STATE]))
    c.append(case("cancel with nothing queued", DATA, ops=[{"op": "cancel"}, STATE]))
    c.append(case("state shows a queued move and the override", DATA, loc(active_path="{root}/data", pending_path="{root}/q"), ops=[STATE]))
    # ---- legacy data
    leg = "app/data"
    full = {f"{leg}/archive.db": "L-db", f"{leg}/archive.db-wal": "L-wal", f"{leg}/archive.db-shm": "L-shm", f"{leg}/metadata_keys.json": "L-keys", f"{leg}/archive_x.session": "L-sess",
            f"{leg}/playback.log": "L-log", f"{leg}/reference_export.txt": "L-ref", f"{leg}/cert/cert.pem": "L-cert", f"{leg}/player_cache/a/b.bin": "L-cache",
            f"{leg}/automation_downloads/d.bin": "L-dl", f"{leg}/automation_torrent_engine/e.bin": "L-te", f"{leg}/media_manager/state.json": "L-mm", f"{leg}/icons/i.png": "L-icon",
            f"{leg}/fonts/f.ttf": "L-font", f"{leg}/ai_evidence/e.json": "L-ev"}
    c.append(case("migrate with nothing to migrate", DATA, ops=[{"op": "migrate"}]))
    c.append(case("migrate a full legacy data folder", DATA, full, ops=[{"op": "migrate"}, {"op": "migrate"}]))
    c.append(case("migrate files that sit beside the application",
                  DATA, {"app/archive.db": "R-db", "app/archive.db-wal": "R-wal", "app/archive.db-shm": "R-shm", "app/archive_x.session": "R-sess", "app/telegram_archive.session": "R-old-sess",
                         "app/cert/cert.pem": "R-cert", "app/config.json": '{"a": 1}', "app/telegram_uploader_config.json": "R-up", "app/telegram_uploader": "R-up-session",
                         "app/telegram_smart_video_router_state.json": "R-router", "app/telegram_channel_cleanup_state.json": "R-clean"}, ops=[{"op": "migrate"}]))
    c.append(case("migrate keeps what is already there", DATA, full | {"data/database/archive.db": "KEEP", "data/settings.json": "KEEP-SETTINGS", "app/config.json": "L-config"}, ops=[{"op": "migrate"}]))
    c.append(case("migrate legacy settings only when there are none", DATA, {"app/config.json": '{"web_username": "old"}'}, ops=[{"op": "migrate"}]))
    c.append(case("migrate the old session in the auth folder", DATA, {"data/auth/telegram_archive.session": "A-sess", "app/archive_x.session": "R-sess"}, ops=[{"op": "migrate"}]))
    c.append(case("migrate the standalone media manager's files", DATA, {"home/TelegramVideoThumbnailUploader/telegram_uploader_config.json": "MM-cfg", "home/TelegramVideoThumbnailUploader/telegram_uploader": "MM-sess",
                                                                        "home/TelegramVideoThumbnailUploader/telegram_uploader.session": "MM-sess2"}, ops=[{"op": "migrate"}]))
    c.append(case("migrate from LOCALAPPDATA", DATA | {"LOCALAPPDATA": "{root}/lad"}, {"lad/TelegramVideoThumbnailUploader/telegram_uploader_config.json": "LAD-cfg", "lad/TelegramVideoThumbnailUploader/telegram_uploader.session": "LAD-sess"}, ops=[{"op": "migrate"}]))
    c.append(case("migrate when the legacy folder exists but lacks the file", DATA, {"app/data/other.txt": "x", "app/archive.db": "R-db"}, ops=[{"op": "migrate"}]))
    # ---- main.py: the default settings file and the obsolete modules
    c.append(case("default settings file is written", DATA, ops=[{"op": "default_config"}]))
    c.append(case("an existing settings file is kept", DATA, {"data/settings.json": '{"web_username": "me"}'}, ops=[{"op": "default_config"}]))
    obs = ["engines/scanner.py", "engines/router_engine.py", "engines/duplicate_engine.py", "engines/thumbnail_repair.py", "engines/intake_watcher.py", "engines/thumbnail_engine.py",
           "telegram/watcher.py", "telegram/scanner.py", "telegram/files.py", "core/assistant.py", "video/__init__.py", "video/analyzer.py", "video/duplicate.py", "video/router.py",
           "video/streamability.py", "video/thumbnail.py", "video/vertical.py"]
    c.append(case("obsolete modules are removed", DATA, {f"pkg/{p}": "# old" for p in obs} | {"pkg/engines/keep.py": "# keep", "pkg/core/assistant_new.py": "# keep"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: the video folder keeps another module", DATA, {f"pkg/{p}": "# old" for p in obs} | {"pkg/video/mine.py": "# keep"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: the video folder holds other files", DATA, {f"pkg/{p}": "# old" for p in obs} | {"pkg/video/notes.txt": "n", "pkg/video/sub/x.bin": "x"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: a folder where a module was", DATA, {"pkg/engines/scanner.py/inner.txt": "i", "pkg/telegram/files.py": "# old"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: a folder named like a module in video", DATA, {"pkg/video/sub.py/x": "x", "pkg/video/__init__.py": "#"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: dot files in video do not count as modules", DATA, {"pkg/video/.py": "#", "pkg/video/mod.": "x", "pkg/video/__init__.py": "#"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: nothing to remove", DATA, {"pkg/core/x.py": "#"}, ops=[{"op": "obsolete"}]))
    c.append(case("obsolete modules: left alone in an installed copy", DATA, {f"pkg/{p}": "# old" for p in obs}, ops=[{"op": "obsolete"}], frozen=True))
    # ---- desktop mode helpers
    for label, env in [("defaults", {}), ("landing", {"ARCHIVE_X_DESKTOP_LANDING": "/app-next/"}), ("landing with a query", {"ARCHIVE_X_DESKTOP_LANDING": "/library?a=1&b=<2>"}),
                       ("landing with quotes", {"ARCHIVE_X_DESKTOP_LANDING": "/a'b\"c"}), ("landing with non-ASCII", {"ARCHIVE_X_DESKTOP_LANDING": "/é日本"}),
                       ("landing, protocol-relative", {"ARCHIVE_X_DESKTOP_LANDING": "//evil.example/"}), ("landing with a colon", {"ARCHIVE_X_DESKTOP_LANDING": "/a:b"}),
                       ("landing with a backslash", {"ARCHIVE_X_DESKTOP_LANDING": "/a\\b"}), ("landing not absolute", {"ARCHIVE_X_DESKTOP_LANDING": "library"}),
                       ("landing too long", {"ARCHIVE_X_DESKTOP_LANDING": "/" + "a" * 199}), ("landing just short enough", {"ARCHIVE_X_DESKTOP_LANDING": "/" + "a" * 198}),
                       ("landing blank", {"ARCHIVE_X_DESKTOP_LANDING": "   "}), ("landing padded", {"ARCHIVE_X_DESKTOP_LANDING": "  /x  "}),
                       ("desktop on", {"ARCHIVE_X_DESKTOP": "1"}), ("desktop yes", {"ARCHIVE_X_DESKTOP": " Yes "}), ("desktop off", {"ARCHIVE_X_DESKTOP": "0"}), ("desktop true", {"ARCHIVE_X_DESKTOP": "TRUE"}),
                       ("desktop on word", {"ARCHIVE_X_DESKTOP": "on"}), ("desktop other word", {"ARCHIVE_X_DESKTOP": "enabled"}), ("desktop empty", {"ARCHIVE_X_DESKTOP": ""}),
                       ("port", {"ARCHIVE_X_DESKTOP_PORT": "4242"}), ("port padded", {"ARCHIVE_X_DESKTOP_PORT": " 77 "}), ("negative port", {"ARCHIVE_X_DESKTOP_PORT": "-5"}),
                       ("bad port", {"ARCHIVE_X_DESKTOP_PORT": "x"}), ("empty port", {"ARCHIVE_X_DESKTOP_PORT": ""}), ("big port", {"ARCHIVE_X_DESKTOP_PORT": "99999999999"}),
                       ("secret padded", {"ARCHIVE_X_LAUNCH_SECRET": "  xx-yy-xx-yy  "}), ("port padded with separator controls", {"ARCHIVE_X_DESKTOP_PORT": "\u001c42\u001f"}),
                       ("landing padded with separator controls", {"ARCHIVE_X_DESKTOP_LANDING": "\u001c/x\u001c"}), ("desktop flag padded with no-break spaces", {"ARCHIVE_X_DESKTOP": "\u00a0ON\u3000"}),
                       ("port in another script", {"ARCHIVE_X_DESKTOP_PORT": "٤٢"})]:
        c.append(case(f"desktop: {label}", DATA | {"ARCHIVE_X_LAUNCH_SECRET": "aaaabbbbccccdddd"} | env, ops=[{"op": "desktop"}]))
    # ---- the secrets file
    steps = [["set", "web_username", "admin"], ["set", "telegram_api_id", 12345], ["set", "tpdb_api_token", "tok"], ["set", "web_username", "root"], ["set", "telegram_api_id", ""],
             ["set", "zero", 0], ["set", "flag", False], ["set", "nothing", None], ["set", "list", [1, "é"]], ["set", "web_username", None], ["set", "unknown", {"a": 1}]]
    c.append(case("secrets: set, replace and delete", DATA | {"ARCHIVE_X_ENCRYPTION_KEY": "test-test-test-test-test-test-test-test-"},
                  ops=[{"op": "secrets", "steps": steps, "keys": ["web_username", "telegram_api_id", "tpdb_api_token", "zero", "flag", "nothing", "list", "unknown", "stashdb_api_key", "web_password_hash", "qbittorrent_api_key", ""]}]))
    return c


def render() -> str:
    return "[\n" + ",\n".join(json.dumps(run_case(c), ensure_ascii=False) for c in cases()) + "\n]\n"


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is out of date; run python tools/parity/gen_layout_vectors.py")
            sys.exit(1)
        print("layout vectors are up to date")
        return
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {text.count(chr(10))} lines")


if __name__ == "__main__":
    main()
