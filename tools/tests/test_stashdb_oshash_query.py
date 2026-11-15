"""StashDB removed `findScenesByFullFingerprints`: every OSHASH lookup in a user's log failed with HTTP 422 ("Did you mean findScenesBySceneFingerprints?").
The lookup must use the current query and read its answer (a list of lists of scenes)."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axstash_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def test_the_oshash_lookup_uses_the_current_stashdb_query(monkeypatch):
    from telegram_archive.metadata import providers
    sent = {}

    def fake_http(url, data=None, headers=None, **kw):
        sent["body"] = json.loads(data)
        return {"data": {"findScenesBySceneFingerprints": [[{"id": "s1", "title": "A scene", "date": "2024-01-02", "studio": None, "performers": [], "tags": [], "images": [], "fingerprints": []}]]}}

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(providers, "_http", fake_http)
    monkeypatch.setattr(providers, "_ensure_cache", lambda db: None)
    monkeypatch.setattr(providers, "_cache_get", lambda *a, **k: (None, False))
    monkeypatch.setattr(providers, "_cache_put", lambda *a, **k: None)
    monkeypatch.setattr(providers, "_throttle", noop)
    monkeypatch.setattr(providers, "_record_provider_call", lambda *a, **k: None)
    monkeypatch.setattr(providers, "_provider_stat", lambda *a, **k: None)
    scenes = asyncio.run(providers.stashdb_by_oshash(object(), "key", "0123456789abcdef"))
    assert "findScenesBySceneFingerprints" in sent["body"]["query"]
    assert "findScenesByFullFingerprints" not in sent["body"]["query"]
    assert sent["body"]["variables"] == {"fps": [[{"hash": "0123456789abcdef", "algorithm": "OSHASH"}]]}
    assert [s["id"] for s in scenes] == ["s1"]
