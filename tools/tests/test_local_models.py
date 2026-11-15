import shutil, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import local_models as lm
from telegram_archive.engines import photo_face_clustering as pf, photo_look_tags as lt

SCRATCH = Path("/tmp/claude-0/-home-user/80af0270-c8de-58bc-ba34-4a44bb436260/scratchpad")


def test_catalog_lists_the_five_models_with_state_and_what_they_power(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(lt, "_root", lambda: tmp_path / "look")
    items = lm.catalog({"runtime": {"ready": False, "runtime": {"exists": False}, "model": {"exists": False}}})
    assert [m["id"] for m in items] == ["llm", "faces", "captions", "content", "looks"]
    by = {m["id"]: m for m in items}
    assert by["llm"]["state"] == "not_set_up" and "Downloads the engine" in by["llm"]["hint"]
    assert by["faces"]["state"] == "not_downloaded" and by["faces"]["installable"]       # opencv is installed here
    assert all(m["powers"] for m in items)
    ready = lm.catalog({"runtime": {"ready": True}})
    assert {m["id"]: m["state"] for m in ready}["llm"] == "ready"
    stopped = lm.catalog({"runtime": {"ready": False, "runtime": {"exists": True}, "model": {"exists": True}}})
    assert {m["id"]: m["state"] for m in stopped}["llm"] == "stopped"


def test_self_test_refuses_to_run_what_is_not_downloaded_and_never_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(lt, "_root", lambda: tmp_path / "look")
    assert lm.self_test("faces")["ok"] is False
    assert lm.self_test("looks")["ok"] is False
    assert lm.self_test("nope")["ok"] is False
    assert not list(tmp_path.rglob("*.onnx"))


@pytest.mark.skipif(not (SCRATCH / "models").exists(), reason="needs the face model files")
def test_faces_model_is_reported_ready_and_runs(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    for f in (SCRATCH / "models").glob("*.onnx"):
        shutil.copy(f, tmp_path / "models" / f.name)
    monkeypatch.setattr(pf, "_cache_root", lambda: tmp_path)
    assert {m["id"]: m["state"] for m in lm.catalog(None)}["faces"] == "ready"
    r = lm.self_test("faces")
    assert r["ok"] and r["ms"] >= 0


@pytest.mark.skipif(not (SCRATCH / "clip").exists(), reason="needs the CLIP files")
def test_look_model_is_reported_ready_and_runs(monkeypatch, tmp_path):
    root = tmp_path / "look"; root.mkdir()
    for n in lt.FILES:
        shutil.copy(SCRATCH / "clip" / n, root / n)
    monkeypatch.setattr(lt, "_root", lambda: root)
    assert {m["id"]: m["state"] for m in lm.catalog(None)}["looks"] == "ready"
    assert lm.self_test("looks")["ok"]


def test_content_detector_self_test():
    pytest.importorskip("nudenet")
    r = lm.self_test("content")
    assert r["ok"], r
