"""Model sizes: which are on offer (recommended first), downloading one, switching to it, and deleting the ones that are not needed."""
import hashlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axmh_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from telegram_archive import config  # noqa: E402
from telegram_archive.core import local_ai_setup as las  # noqa: E402
from telegram_archive.core import local_models as lm  # noqa: E402
from telegram_archive.core import model_hub as hub  # noqa: E402
from telegram_archive.engines import photo_face_clustering as pf  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "PLAYER_CACHE_DIR", tmp_path / "cache" / "player_cache")
    monkeypatch.setattr(config, "AI_MODELS_DIR", tmp_path / "ai" / "models")
    monkeypatch.setattr(lm, "CONTROL", {})
    return tmp_path


def put(family, vid, fill=b"x"):
    """Pretend a variant is downloaded."""
    root = hub.variant_dir(family, vid)
    root.mkdir(parents=True, exist_ok=True)
    for f in hub.variant(family, vid).files:
        (root / f.name).write_bytes(fill * 2048)


def rows(family):
    return {v["id"]: v for v in lm.variants_of(family)}


def test_the_pinned_face_checksums_are_the_ones_the_face_code_checks():
    sums = {f.name: f.sha256 for f in hub.variant("faces", "standard").files}
    assert sums == {"face_detection_yunet_2023mar.onnx": pf._YUNET_SHA256, "face_recognition_sface_2021dec.onnx": pf._SFACE_SHA256}


def test_every_family_lists_the_recommended_size_first_then_bigger_then_lighter(home):
    order = {m["id"]: [v["id"] for v in m["variants"]] for m in lm.catalog(None)}
    assert order["llm"][0] == "balanced" and order["llm"][1:] == ["strong", "small"]
    assert order["captions"] == ["small", "medium", "large-v3-turbo", "base"]
    assert order["looks"] == ["b32", "b16"] and order["faces"] == ["standard"] and order["content"] == ["fast"]
    for m in lm.catalog(None):
        assert sum(v["recommended"] for v in m["variants"]) == 1 and m["variants"][0]["recommended"]


def test_nothing_is_downloaded_until_asked_and_the_recommended_size_is_in_use_by_default(home):
    for m in lm.catalog(None):
        assert not any(v["installed"] for v in m["variants"] if m["id"] != "faces"), m["id"]
        assert m["selected"] == m["variants"][0]["id"]
    assert not (home / "cache").exists()


def test_a_download_goes_to_its_own_folder_is_checked_and_keeps_the_old_folder_for_the_recommended_size(home, monkeypatch):
    payload = {"a.bin": b"first" * 400, "b.bin": b"second" * 400}
    fake = hub.Variant("tiny", "Tiny", 1, "", False, tuple(hub.File(n, ("https://x/" + n,), hashlib.sha256(b).hexdigest(), len(b)) for n, b in payload.items()))
    monkeypatch.setitem(hub.FAMILIES, "looks", [*hub.FAMILIES["looks"], fake])
    seen = []

    def fake_download(url, dest, *, sha256, size_hint, on_bytes, cancel, opener=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload[dest.name])
        on_bytes(len(payload[dest.name]), size_hint)
    monkeypatch.setattr(las, "download", fake_download)
    root = hub.fetch("looks", "tiny", lambda d, t: seen.append((d, t)))
    assert root == hub.variant_dir("looks", "tiny") and root.parent == hub.variant_dir("looks", "b32")
    assert hub.installed("looks", "tiny") and seen[-1] == (len(payload["a.bin"]) + len(payload["b.bin"]),) * 2
    calls = []
    monkeypatch.setattr(las, "download", lambda *a, **k: calls.append(a))
    hub.fetch("looks", "tiny")
    assert calls == []                                                       # what is there and matches is not fetched again
    (root / "a.bin").write_bytes(b"damaged")
    monkeypatch.setattr(las, "download", fake_download)
    hub.fetch("looks", "tiny")
    assert (root / "a.bin").read_bytes() == payload["a.bin"]                 # a damaged file is fetched again


def test_a_mirror_is_tried_when_the_first_address_fails(home, monkeypatch):
    f = hub.File("m.bin", ("https://one/m.bin", "https://two/m.bin"), hashlib.sha256(b"ok").hexdigest(), 2)
    monkeypatch.setitem(hub.FAMILIES, "content", [hub.Variant("fast", "Standard", 1, "", True, (f,))])
    tried = []

    def dl(url, dest, *, sha256, size_hint, on_bytes, cancel, opener=None):
        tried.append(url)
        if "one" in url:
            raise OSError("down")
        dest.write_bytes(b"ok")
    monkeypatch.setattr(las, "download", dl)
    hub.fetch("content", "fast")
    assert tried == ["https://one/m.bin", "https://two/m.bin"] and (hub.variant_dir("content", "fast") / "m.bin").read_bytes() == b"ok"


def test_choosing_a_size_is_remembered_for_the_engines_and_must_be_a_real_one(home):
    assert hub.selected("looks") == "b32" and hub.active_dir("looks") == hub.variant_dir("looks", "b32")
    hub.select("looks", "b16")
    assert hub.selected("looks") == "b16" and hub.active_dir("looks").name == "b16"
    with pytest.raises(KeyError):
        hub.select("looks", "huge")
    (hub.models_root() / "choices.json").write_text("{not json")
    assert hub.selected("looks") == "b32"                                    # a damaged file means the recommended size, not a crash


def test_a_size_that_is_not_downloaded_cannot_be_chosen(home):
    with pytest.raises(ValueError, match="Download it first"):
        lm.select_variant("looks", "b16")
    put("looks", "b16")
    assert lm.select_variant("looks", "b16") == {"selected": "b16"} and rows("looks")["b16"]["selected"]


def test_the_size_in_use_is_kept_while_another_is_there_to_switch_to_and_the_rest_can_go(home):
    put("looks", "b32"); put("looks", "b16")
    with pytest.raises(ValueError, match="in use"):
        lm.delete_variant("looks", "b32")                                    # b32 is the one in use and b16 is downloaded
    lm.delete_variant("looks", "b16"); assert not rows("looks")["b16"]["installed"]
    assert not hub.variant_dir("looks", "b16").exists()                      # its folder goes with it
    assert lm.delete_variant("looks", "b32") >= 0 and not rows("looks")["b32"]["installed"]   # the only one downloaded: it may go
    assert lm.delete_variant("looks", "b16") == 0                            # nothing there: nothing to do


def test_deleting_removes_half_downloaded_files_too(home):
    put("content", "fast")
    part = hub.variant_dir("content", "fast") / "320n.onnx.part"
    part.write_bytes(b"x" * 100)
    lm.delete_variant("content", "fast")
    assert not part.exists() and not hub.installed("content", "fast")


def test_caption_sizes_are_found_by_their_own_folder_and_switch_through_the_caption_service(home):
    def fake_whisper(name):
        d = lm._whisper_folder(name) / "snapshots" / "abc"; d.mkdir(parents=True); (d / "model.bin").write_bytes(b"x" * 10)
    fake_whisper("small"); fake_whisper("large-v3-turbo")
    assert {k: v["installed"] for k, v in rows("captions").items()} == {"small": True, "medium": False, "large-v3-turbo": True, "base": False}
    state = {"model": "small"}
    lm.CONTROL.update(get=lambda: state["model"], set=lambda n: state.update(model=n))
    assert rows("captions")["small"]["selected"]
    lm.select_variant("captions", "large-v3-turbo")
    assert state["model"] == "large-v3-turbo" and rows("captions")["large-v3-turbo"]["selected"]
    with pytest.raises(ValueError):
        lm.delete_variant("captions", "large-v3-turbo")                      # in use, and "small" is there to switch to
    lm.delete_variant("captions", "small")
    assert not lm._whisper_folder("small").exists() and lm._whisper_folder("large-v3-turbo").exists()   # only that size went


def test_the_assistant_sizes_switch_by_pointing_it_at_the_file_and_stop_it_first(home):
    class FakeAI:
        def __init__(self):
            self.model_path = config.AI_MODELS_DIR / las.MODELS["balanced"].file; self.log = []

        def stop(self):
            self.log.append("stop")

        def configure(self, **kw):
            self.log.append(("configure", kw)); self.model_path = Path(kw["model_path"])
    ai = FakeAI()
    config.AI_MODELS_DIR.mkdir(parents=True)
    for k in ("balanced", "strong"):
        (config.AI_MODELS_DIR / las.MODELS[k].file).write_bytes(b"g" * 100)
    got = {v["id"]: v for v in lm.variants_of("llm", ai.model_path.name)}
    assert got["balanced"]["selected"] and got["balanced"]["installed"] and got["strong"]["installed"] and not got["small"]["installed"]
    lm.select_variant("llm", "strong", ai=ai)
    assert ai.log[0] == "stop" and ai.log[1][1]["model_path"].endswith(las.MODELS["strong"].file) and ai.log[1][1]["model_id"] == las.MODELS["strong"].model_id
    with pytest.raises(ValueError):
        lm.delete_variant("llm", "strong", ai=ai)                            # in use
    lm.delete_variant("llm", "balanced", ai=ai)
    assert not (config.AI_MODELS_DIR / las.MODELS["balanced"].file).exists() and (config.AI_MODELS_DIR / las.MODELS["strong"].file).exists()


# ---- the routes -----------------------------------------------------------------------------------------------------------------------

def _client(tmp_path):
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    from telegram_archive.core.database import Database
    db = Database(tmp_path / "a.db")
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, H


def test_routes_list_sizes_switch_delete_and_refuse_what_is_not_allowed(home):
    c, H = _client(home)
    models = {m["id"]: m for m in c.get("/api/ai/models").json()["models"]}
    assert [v["id"] for v in models["looks"]["variants"]] == ["b32", "b16"] and models["looks"]["selected"] == "b32"
    assert c.post("/api/ai/models/looks/variants/b16/select", headers=H).status_code == 409        # not downloaded
    assert c.post("/api/ai/models/looks/variants/nope/select", headers=H).status_code == 404
    assert c.post("/api/ai/models/nothing/variants/x/install", headers=H).status_code == 404
    put("looks", "b32"); put("looks", "b16")
    assert c.post("/api/ai/models/looks/variants/b16/select", headers=H).json() == {"ok": True, "selected": "b16"}
    assert {v["id"]: v["selected"] for v in c.get("/api/ai/models").json()["models"][4]["variants"]} == {"b32": False, "b16": True}
    assert c.delete("/api/ai/models/looks/variants/b16", headers=H).status_code == 409             # in use
    gone = c.delete("/api/ai/models/looks/variants/b32", headers=H)
    assert gone.status_code == 200 and gone.json()["freed_mb"] >= 0
    assert [v["installed"] for v in c.get("/api/ai/models").json()["models"][4]["variants"]] == [False, True]


def test_a_download_started_from_the_page_reports_progress_and_finishes(home, monkeypatch):
    c, H = _client(home)
    release = threading.Event()

    def slow(model_id, variant_id=None, on_progress=None, cancel=None):
        on_progress(50, 100)
        release.wait(5)
        put(model_id, variant_id)
    monkeypatch.setattr(lm, "download", slow)
    assert c.post("/api/ai/models/looks/variants/b16/install", headers=H).json()["started"] is True
    for _ in range(100):
        looks = next(m for m in c.get("/api/ai/models").json()["models"] if m["id"] == "looks")
        if looks["state"] == "downloading":
            break
        time.sleep(0.02)
    b16 = next(v for v in looks["variants"] if v["id"] == "b16")
    assert looks["state"] == "downloading" and b16["busy"] and b16["progress"] == 50
    assert c.delete("/api/ai/models/looks/variants/b16", headers=H).status_code == 409             # not while it is downloading
    assert c.post("/api/ai/models/looks/variants/b16/install", headers=H).json()["already_running"] is True
    release.set()
    for _ in range(100):
        looks = next(m for m in c.get("/api/ai/models").json()["models"] if m["id"] == "looks")
        if looks["state"] != "downloading":
            break
        time.sleep(0.02)
    assert next(v for v in looks["variants"] if v["id"] == "b16")["installed"]
