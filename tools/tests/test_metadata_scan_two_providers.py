"""Two metadata providers (StashDB first, ThePornDB second): when each is asked, and what happens when they disagree."""
import pytest

import metadata_scan_harness as h
from telegram_archive.metadata import matching, providers as prov

HASH = "0123456789abcdef"


def stash(**kw):
    b = {"source": "stashdb", "id": "s1", "title": "Office Affair", "date": "2025-09-28", "studio": "Brazzers", "site": "Brazzers", "studio_id": "1",
         "studio_url": "https://brazzers.com", "scene_type": "Scene", "image": "i", "thumbnail": "t", "profile": "p", "description": "d",
         "performers": ["Ava Stone", "Mick Blue"],
         "performer_details": [{"name": "Ava Stone", "id": "a1", "provider": "stashdb"}, {"name": "Mick Blue", "id": "m1", "provider": "stashdb"}], "tags": ["Office"]}
    b.update(kw)
    return b


def tpdb(**kw):
    b = {"source": "theporndb", "id": "t1", "title": "Office Affair", "date": "2025-09-28", "studio": "Brazzers", "site": "Brazzers",
         "performers": ["Ava Stone", "Mick Blue"],
         "performer_details": [{"name": "Ava Stone", "id": "9", "provider": "theporndb"}, {"name": "Mick Blue", "id": "8", "provider": "theporndb"}], "tags": ["Office", "Blonde"]}
    b.update(kw)
    return b


def people(*names, provider):
    return {"performers": list(names), "performer_details": [{"name": n, "id": str(i), "provider": provider} for i, n in enumerate(names)]}


@pytest.fixture
def keys():
    db = h.fresh_db()
    old = {k: db.setting(k) for k in ("stashdb_api_key", "tpdb_api_token", "tpdb_token")}
    def set_keys(stash_key="s-key", tpdb_key="t-key"):
        db.set_setting("stashdb_api_key", stash_key)
        db.set_setting("tpdb_api_token", tpdb_key)
        db.set_setting("tpdb_token", "")
    set_keys()
    yield db, set_keys
    for k, v in old.items():
        db.set_setting(k, v if v is not None else "")


class Calls:
    def __init__(self, monkeypatch, *, stash_hash=None, tpdb_hash=None, stash_clues=None, tpdb_clues=None, stash_error=None, tpdb_error=None):
        self.log = []
        async def sh(db, key, oshash):
            self.log.append("stash_hash")
            if stash_error: raise RuntimeError(stash_error)
            return [dict(x) for x in (stash_hash or [])]
        async def th(db, key, oshash):
            self.log.append("tpdb_hash")
            if tpdb_error: raise RuntimeError(tpdb_error)
            return [dict(x) for x in (tpdb_hash or [])]
        async def ss(db, key, term, limit=10):
            self.log.append("stash_clues")
            if stash_error: raise RuntimeError(stash_error)
            return [dict(x) for x in (stash_clues or [])]
        async def sd(db, key, date, limit=20):
            return []
        async def sy(db, key, year, limit=20):
            return []
        async def tp(db, key, term, **kw):
            self.log.append("tpdb_clues")
            if tpdb_error: raise RuntimeError(tpdb_error)
            return [dict(x) for x in (tpdb_clues or [])]
        for name, fn in (("stashdb_by_oshash", sh), ("tpdb_by_hash", th), ("stashdb_search_scenes", ss), ("stashdb_scenes_by_date", sd),
                         ("stashdb_scenes_by_year", sy), ("tpdb_parse", tp)):
            monkeypatch.setattr(prov, name, fn)


def ask(db, filename="Brazzers.25.09.28.Ava.Stone.Mick.Blue.Office.Affair.mp4", oshash=HASH, **kw):
    return h.run(prov.identify(db, oshash=oshash, filename=filename, **kw))


# ---------------------------------------------------------------- who is asked, and when -------------------------------------------------------
def test_a_complete_stashdb_fingerprint_match_never_bothers_the_second_provider(keys, monkeypatch):
    db, _ = keys
    c = Calls(monkeypatch, stash_hash=[stash()])
    out = ask(db)
    assert c.log == ["stash_hash"] and out["matches"][0]["source"] == "stashdb"


def test_a_sparse_stashdb_record_is_filled_in_from_the_other_provider_without_changing_who_it_is(keys, monkeypatch):
    db, _ = keys
    sparse = stash(description=None, image=None, thumbnail=None)
    c = Calls(monkeypatch, stash_hash=[sparse], tpdb_hash=[tpdb(description="from tpdb", image="tp-image")])
    out = ask(db, hash_only=True)
    m = out["matches"][0]
    assert "tpdb_hash" in c.log and m["source"] == "stashdb" and m["id"] == "s1"
    assert m["description"] == "from tpdb" and m["title"] == "Office Affair"            # filled, and StashDB's own values kept
    assert m["field_provenance"]["description"] != m["field_provenance"]["title"]


def test_when_stashdb_does_not_know_the_file_thepornDB_answers(keys, monkeypatch):
    db, _ = keys
    Calls(monkeypatch, stash_hash=[], tpdb_hash=[tpdb()])
    out = ask(db)
    assert out["method"] == "theporndb oshash" and out["matches"][0]["source"] == "theporndb"


def test_an_error_from_one_provider_does_not_stop_the_other(keys, monkeypatch):
    db, _ = keys
    Calls(monkeypatch, stash_error="StashDB is down", tpdb_hash=[tpdb()], tpdb_clues=[tpdb()])
    out = ask(db)
    assert out["matches"] and out["matches"][0]["source"] == "theporndb" and any("StashDB" in e for e in out["errors"])


def test_when_both_providers_fail_the_error_is_reported_not_a_false_no_match(keys, monkeypatch):
    db, _ = keys
    Calls(monkeypatch, stash_error="StashDB is down", tpdb_error="ThePornDB is down")
    out = ask(db)
    assert not out["matches"] and len(out["errors"]) >= 2


def test_with_only_one_key_configured_the_other_provider_is_never_called(keys, monkeypatch):
    db, set_keys = keys
    set_keys(stash_key="s-key", tpdb_key="")
    c = Calls(monkeypatch, stash_hash=[], stash_clues=[stash()], tpdb_hash=[tpdb()], tpdb_clues=[tpdb()])
    ask(db)
    assert not any(x.startswith("tpdb") for x in c.log)
    set_keys(stash_key="", tpdb_key="t-key")
    c2 = Calls(monkeypatch, stash_hash=[stash()], tpdb_hash=[tpdb()], tpdb_clues=[tpdb()])
    out = ask(db)
    assert not any(x.startswith("stash") for x in c2.log) and out["matches"][0]["source"] == "theporndb"


# ---------------------------------------------------------------- when the two disagree ---------------------------------------------------------
def merge(a, b):
    return prov._merge_search_candidates([a, b])


def test_the_same_scene_from_both_providers_is_one_candidate_with_stashdb_values_first():
    out = merge(stash(), tpdb(title="Office Affair!", date="2025-09-29"))
    assert len(out) == 1 and out[0]["source"] == "stashdb" and out[0]["title"] == "Office Affair" and out[0]["cross_provider_confirmed"] is True
    assert {c["provider"] for c in out[0]["cross_provider_candidates"]} == {"stashdb", "theporndb"}
    assert set(out[0]["tags"]) == {"Office", "Blonde"}


def test_a_name_spelled_slightly_differently_is_one_performer_not_two():
    out = merge(stash(), tpdb(**people("Ava Stones", "Mick Blue", provider="theporndb")))
    assert len(out) == 1 and out[0]["performers"] == ["Ava Stone", "Mick Blue"]
    ava = next(d for d in out[0]["performer_details"] if d["name"] == "Ava Stone")
    assert "Ava Stones" in ava["aliases"] and ava["provider_ids"]["stashdb"] == "a1"


def test_accents_and_an_ampersand_do_not_make_two_scenes_or_two_people():
    out = merge(stash(title="Mom & Son", **people("Élodie Raye", "Mick Blue", provider="stashdb")), tpdb(title="Mom and Son", **people("Elodie Raye", "Mick Blue", provider="theporndb")))
    assert len(out) == 1 and len(out[0]["performer_details"]) == 2


def test_a_studio_name_in_front_of_the_title_does_not_split_one_scene_in_two():
    assert len(merge(stash(), tpdb(title="Brazzers - Office Affair"))) == 1


def test_a_provider_that_lists_extra_people_adds_them():
    out = merge(stash(), tpdb(**people("Ava Stone", "Mick Blue", "Zed Quill", provider="theporndb")))
    assert out[0]["performers"] == ["Ava Stone", "Mick Blue", "Zed Quill"] and "cast_conflict" not in out[0]


def test_two_providers_that_name_different_people_keep_stashdbs_cast_and_flag_the_dispute():
    out = merge(stash(), tpdb(**people("Ava Stone", "Rico Strong", provider="theporndb")))
    assert out[0]["performers"] == ["Ava Stone", "Mick Blue"]                           # not Rico Strong as well
    assert out[0]["cast_conflict"] == {"primary_only": ["Mick Blue"], "fallback_only": ["Rico Strong"]}


def _confidence_for(db, monkeypatch, merged):
    async def fake_identify(db_, **kw):
        return {"matches": [dict(x) for x in merged], "method": "cross-provider fallback clue search", "errors": []}

    monkeypatch.setattr(matching, "identify", fake_identify)
    return h.run(matching.identify_with_confidence(db, oshash=None, filename="Brazzers.25.09.28.Ava.Stone.Mick.Blue.Office.Affair.mp4"))


def test_a_disputed_cast_is_not_applied_automatically_but_stays_for_review(keys, monkeypatch):
    db, _ = keys
    agreed = _confidence_for(db, monkeypatch, prov._merge_search_candidates([stash(), tpdb()]))
    assert agreed["confidence"] == "HIGH"                                  # the same evidence, no dispute: applied automatically
    disputed = _confidence_for(db, monkeypatch, prov._merge_search_candidates([stash(), tpdb(**people("Ava Stone", "Rico Strong", provider="theporndb"))]))
    assert disputed["confidence"] == "MEDIUM" and disputed["best"] is not None
    assert "the two providers disagree on the cast" in disputed["best"]["_archive_reason"]


def test_an_exact_fingerprint_still_applies_with_stashdbs_cast_even_if_the_cast_is_disputed(keys, monkeypatch):
    db, _ = keys
    Calls(monkeypatch, stash_hash=[stash(description=None)], tpdb_hash=[tpdb(**people("Ava Stone", "Rico Strong", provider="theporndb"))])
    out = h.run(matching.identify_with_confidence(db, oshash=HASH, filename="x.mp4"))
    assert out["confidence"] == "HIGH" and out["best"]["performers"] == ["Ava Stone", "Mick Blue"]


def test_the_same_content_on_two_different_sites_stays_two_candidates_for_review():
    out = merge(stash(), tpdb(studio="Brazzers Exxtra", site="Brazzers Exxtra"))
    assert len(out) == 2 and {o["source"] for o in out} == {"stashdb", "theporndb"}


def test_two_scenes_with_different_titles_are_not_merged_just_because_the_cast_matches():
    out = merge(stash(), tpdb(title="A Completely Different Movie", date="2019-01-01"))
    assert len(out) == 2
