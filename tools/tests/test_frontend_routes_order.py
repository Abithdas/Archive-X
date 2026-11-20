import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from telegram_archive.web.routes_frontend import register_frontend_routes


def make(tmp_path, frontend_last):
    (tmp_path / "index.html").write_text("<html>app</html>")
    app = FastAPI()
    if not frontend_last:
        register_frontend_routes(app.router, dist_dir=tmp_path)

    @app.get("/api/ai/status")          # a route added to the app itself, like the AI assistant and the test bridge
    async def status():
        return {"ok": True}
    if frontend_last:
        register_frontend_routes(app.router, dist_dir=tmp_path)
    return TestClient(app)


def test_registered_early_the_catch_all_shadows_later_routes():
    import tempfile
    assert make(Path(tempfile.mkdtemp()), frontend_last=False).get("/api/ai/status").status_code == 404


def test_registered_last_later_routes_work_and_the_app_still_opens(tmp_path):
    c = make(tmp_path, frontend_last=True)
    assert c.get("/api/ai/status").json() == {"ok": True}
    assert c.get("/photos").text == "<html>app</html>"          # client-side routes still get the app
    assert c.get("/api/unknown").status_code == 404              # unknown backend paths stay 404


def test_service_worker_and_manifest_are_always_revalidated(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "sw.js").write_text("//sw")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a-1.js").write_text("x")
    client = make(tmp_path, True)
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"
    m = client.get("/manifest.webmanifest")
    assert m.headers["cache-control"] == "no-cache" and m.headers["content-type"].startswith("application/manifest+json")
    assert "immutable" in client.get("/assets/a-1.js").headers["cache-control"]
