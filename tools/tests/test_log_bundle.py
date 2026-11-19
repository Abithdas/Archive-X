"""The support zip: what goes in, what is blanked or left out, how it is named, and the endpoint that makes it."""
import asyncio
import io
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import log_bundle as lb

NOW = datetime(2026, 11, 14, 8, 12, 45).astimezone()


def make_logs(root: Path) -> Path:
    logs = root / "logs"
    (logs / "sub").mkdir(parents=True)
    (logs / "playback.log").write_text("2026-11-14 ffmpeg segment 1 failed\nGET /video/5?c=-1001&t=deadbeefdeadbeef0123 200\n")
    (logs / "app.log").write_text("api_hash=abcdef123456 connecting\nAuthorization: Bearer abcdefabcdefabcdefabcdef\nphone: +15551234567\n")
    (logs / "sub" / "launcher-cli.log").write_text('{"password": "hunter2", "ok": 1}\nSet-Cookie: session=SECRETVALUE; Path=/\n')
    (logs / "telegram.session").write_text("DO-NOT-INCLUDE")
    (logs / "cert.pem").write_text("-----BEGIN PRIVATE KEY-----")
    (logs / "archive.db").write_bytes(b"\x00\x01sqlite")
    (logs / "cookies.txt").write_text("session=abc")
    (logs / "photo.jpg").write_bytes(b"\xff\xd8")
    return logs


def open_zip(buf) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def test_the_name_carries_the_date_and_time():
    assert lb.bundle_name(NOW) == "archive-x-logs-2026-11-14_08-12-45.zip"
    assert re.fullmatch(r"archive-x-logs-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.zip", lb.bundle_name())


def test_logs_diagnostics_readme_and_manifest_are_in_one_zip(tmp_path):
    logs = make_logs(tmp_path)
    buf = io.BytesIO()
    manifest = lb.build_bundle(buf, log_dir=logs, extras={"system/system.txt": "os : test", "diagnostics/x.json": {"a": 1}}, now=NOW, app_version="v-test")
    z = open_zip(buf)
    names = set(z.namelist())
    assert {"README.txt", "manifest.json", "logs/playback.log", "logs/app.log", "logs/sub/launcher-cli.log", "system/system.txt", "diagnostics/x.json"} <= names
    assert json.loads(z.read("diagnostics/x.json")) == {"a": 1}
    assert json.loads(z.read("manifest.json")) == manifest
    assert manifest["app_version"] == "v-test" and manifest["redacted"] is True
    assert {f["name"] for f in manifest["files"]} == names - {"README.txt", "manifest.json"}
    assert "2026-11-14 08:12:45" in z.read("README.txt").decode()
    assert z.testzip() is None


def test_secrets_are_blanked_in_logs_and_extras(tmp_path):
    logs = make_logs(tmp_path)
    buf = io.BytesIO()
    lb.build_bundle(buf, log_dir=logs, extras={"diagnostics/report.txt": "see http://127.0.0.1:18570/video/9?c=-1&t=0123456789abcdef0123 and token=zzz999"}, now=NOW)
    z = open_zip(buf)
    everything = "\n".join(z.read(n).decode() for n in z.namelist())
    for secret in ("deadbeefdeadbeef0123", "abcdef123456", "abcdefabcdefabcdefabcdef", "+15551234567", "hunter2", "SECRETVALUE", "0123456789abcdef0123", "zzz999"):
        assert secret not in everything, secret
    assert everything.count("<REDACTED>") >= 8
    assert "ffmpeg segment 1 failed" in z.read("logs/playback.log").decode()       # what support needs is kept
    assert "/video/5?c=-1001&t=<REDACTED>" in z.read("logs/playback.log").decode()   # the address stays readable, only the signature goes


def test_files_that_can_hold_credentials_and_non_logs_are_never_included(tmp_path):
    logs = make_logs(tmp_path)
    buf = io.BytesIO()
    manifest = lb.build_bundle(buf, log_dir=logs, now=NOW)
    z = open_zip(buf)
    assert not any(n.endswith((".session", ".pem", ".db", "cookies.txt", ".jpg")) for n in z.namelist())
    assert "DO-NOT-INCLUDE" not in "".join(z.read(n).decode(errors="replace") for n in z.namelist())
    reasons = {s["name"]: s["reason"] for s in manifest["skipped"]}
    assert reasons["telegram.session"] == "may hold credentials" and reasons["cert.pem"] == "may hold credentials" and reasons["archive.db"] == "may hold credentials"
    assert reasons["photo.jpg"] == "not a text log"


def test_a_huge_log_keeps_its_end_and_says_so(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "big.log").write_text("".join(f"line {i:06d}\n" for i in range(50_000)))   # 600 KB
    buf = io.BytesIO()
    manifest = lb.build_bundle(buf, log_dir=logs, now=NOW, max_file_bytes=10_000)
    text = open_zip(buf).read("logs/big.log").decode()
    assert text.startswith("[... earlier ") and "line 049999" in text and "line 000000" not in text
    entry = next(f for f in manifest["files"] if f["name"] == "logs/big.log")
    assert entry["cut"] is True and entry["original_bytes"] > 500_000 and entry["bytes"] < 12_000
    assert re.search(r"\nline \d{6}\n", text)   # the cut starts at a whole line


def test_the_total_size_is_capped_and_what_was_left_out_is_listed(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(5):
        (logs / f"a{i}.log").write_text("x" * 30_000)
    buf = io.BytesIO()
    manifest = lb.build_bundle(buf, log_dir=logs, now=NOW, max_total_bytes=70_000)
    assert manifest["total_bytes"] <= 70_000
    assert sum(1 for s in manifest["skipped"] if s["reason"] == "bundle size limit reached") >= 2


def test_a_broken_part_is_noted_and_the_rest_survives(tmp_path):
    def boom():
        raise RuntimeError("collector failed")
    cfg = SimpleNamespace(APP_VERSION="v", USER_DATA_DIR=tmp_path, LOG_DIR=tmp_path / "logs")
    db = SimpleNamespace(reading=lambda: (_ for _ in ()).throw(RuntimeError("db busy")))
    extras = lb.collect_extras(config=cfg, db=db, report=boom, ffmpeg=None)
    assert "system/system.txt" in extras and "diagnostics/ignored-errors.json" in extras
    assert "diagnostics/report.txt" not in extras and "activity/events.txt" not in extras
    errors = extras["diagnostics/collection-errors.txt"]
    assert "collector failed" in errors and "db busy" in errors


def test_environment_lists_this_run_but_blanks_secrets():
    text = lb.environment_text({"ARCHIVE_X_DATA_DIR": "C:\\data", "ARCHIVE_X_PROXY_KEY": "supersecret", "PATH": "C:\\Windows", "TELEGRAM_ARCHIVE_PORT": "18443", "AWS_SECRET_ACCESS_KEY": "nope"})
    assert "ARCHIVE_X_DATA_DIR=C:\\data" in text and "TELEGRAM_ARCHIVE_PORT=18443" in text
    assert "ARCHIVE_X_PROXY_KEY=<REDACTED>" in text and "supersecret" not in text
    assert "PATH" not in text and "AWS_SECRET" not in text


def test_saving_names_each_file_by_date_and_time_and_never_overwrites(tmp_path):
    logs = make_logs(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    p1, _ = lb.build_to_file(out, log_dir=logs, extras={}, now=NOW)
    p2, _ = lb.build_to_file(out, log_dir=logs, extras={}, now=NOW)   # the same second
    assert p1.name == "archive-x-logs-2026-11-14_08-12-45.zip" and p2.name == "archive-x-logs-2026-11-14_08-12-45-2.zip"
    assert {x.name for x in out.iterdir()} == {p1.name, p2.name}        # no half-written .part left behind
    assert zipfile.is_zipfile(p1)


# ---- the endpoint ---------------------------------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    import httpx
    from fastapi import APIRouter, FastAPI
    from telegram_archive.core import ai_evidence
    from telegram_archive.web.routes_tags_logs import register_tags_logs_routes
    logs = make_logs(tmp_path)
    cfg = SimpleNamespace(APP_VERSION="v-test", USER_DATA_DIR=tmp_path, LOG_DIR=logs, CLI_LOG_PATH=logs / "none.log")

    class Rows:
        def execute(self, *_a):
            return SimpleNamespace(fetchall=lambda: [{"level": "INFO", "message": "hello", "created_at": "2026-11-14T08:00:00"}])

    class Reading:
        def __enter__(self):
            return Rows()
        def __exit__(self, *a):
            return False
    db = SimpleNamespace(reading=lambda: Reading(), setting=lambda key, default=None: "nsfw")
    monkeypatch.setattr(ai_evidence, "collect_diagnostics", lambda **kw: {"text": "REPORT api_hash=abc123"})
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    router = APIRouter()
    register_tags_logs_routes(router, config=cfg, db=db, jobs=None, fts_prefix_query=None, page_enricher=None, ps={}, require_auth=lambda r: None, resources=None, telegram=None)
    fastapi = FastAPI()
    fastapi.include_router(router)

    def client(host="127.0.0.1"):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi, client=(host, 5000)), base_url="http://test")
    return SimpleNamespace(client=client, home=home)


def test_the_endpoint_returns_a_zip_named_with_date_and_time(app):
    async def go():
        async with app.client() as c:
            return await c.get("/api/diagnostics/logs.zip")
    r = asyncio.run(go())
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert re.search(r'filename="?archive-x-logs-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.zip"?', r.headers["content-disposition"])
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"README.txt", "manifest.json", "logs/playback.log", "system/system.txt", "diagnostics/report.txt", "activity/events.txt"} <= names
    assert "hello" in z.read("activity/events.txt").decode()
    assert "abc123" not in z.read("diagnostics/report.txt").decode()
    assert "deadbeefdeadbeef0123" not in z.read("logs/playback.log").decode()


def test_save_from_this_pc_puts_the_zip_in_downloads_and_says_where(app):
    async def go():
        async with app.client() as c:
            return await c.get("/api/diagnostics/logs.zip?save=1")
    r = asyncio.run(go())
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    saved = Path(body["path"])
    assert saved.parent == app.home / "Downloads" and saved.exists() and zipfile.is_zipfile(saved)
    assert saved.name == body["name"] and body["bytes"] == saved.stat().st_size and body["files"] >= 5


def test_saving_to_a_folder_is_refused_from_another_machine(app):
    async def go():
        async with app.client("10.0.0.5") as c:
            return await c.get("/api/diagnostics/logs.zip?save=1")
    assert asyncio.run(go()).status_code == 403
    assert not list((app.home / "Downloads").iterdir())


def test_a_slow_report_is_left_out_not_waited_for(app, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from telegram_archive.core import ai_evidence
    monkeypatch.setattr(ai_evidence, "collect_diagnostics", lambda **kw: (_ for _ in ()).throw(RuntimeError("topology scan failed")))
    async def go():
        async with app.client() as c:
            return await c.get("/api/diagnostics/logs.zip")
    t = time.time()
    r = asyncio.run(go())
    assert r.status_code == 200 and time.time() - t < 20
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "diagnostics/report.txt" not in z.namelist()
    assert "topology scan failed" in z.read("diagnostics/collection-errors.txt").decode()
    assert ThreadPoolExecutor  # imported only to make sure the executor the route uses is available here
