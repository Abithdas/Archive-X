import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.metadata import providers


class FakeDb:
    def __init__(self): self.cache = {}
    def setting(self, k): return "key" if k == "stashdb_api_key" else ""


def test_typed_name_beats_the_providers_own_order_and_empty_results_are_not_cached(monkeypatch):
    rows = [{"id": str(i), "name": n, "aliases": []} for i, n in enumerate(["Stacy Adams", "Stacey Lane", "Stacy Cruz", "Anastasia Star"])]
    calls = []
    monkeypatch.setattr(providers, "_cache_get", lambda db, p, k, max_age=0: (db.cache.get(k), k in db.cache))
    monkeypatch.setattr(providers, "_cache_put", lambda db, p, k, v: db.cache.__setitem__(k, v))
    async def no_wait(_): return None
    monkeypatch.setattr(providers, "_throttle", no_wait)
    monkeypatch.setattr(providers, "_stashdb_graphql", lambda db, key, q, v: calls.append(v) or {})
    monkeypatch.setattr(providers, "_stashdb_rows", lambda data, name: [] if len(calls) == 1 else rows)
    monkeypatch.setattr(providers, "_http_entity_image", lambda row, performer=True: "")
    db = FakeDb()
    assert asyncio.run(providers.search_performer_providers(db, "stacy cr")) == []        # nothing found: not remembered
    out = asyncio.run(providers.search_performer_providers(db, "stacy cr"))
    assert [r["name"] for r in out][0] == "Stacy Cruz"
    assert calls[-1]["limit"] >= 20
