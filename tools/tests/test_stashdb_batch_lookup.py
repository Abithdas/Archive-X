"""StashDB fingerprint lookups, batched: 40 files per request instead of one request (and 0.5 s of enforced waiting) per file."""
import json

import pytest

import metadata_scan_harness as h
from telegram_archive.metadata import providers as prov


def hexhash(i):
    return f"{i:016x}"


class FakeStash:
    """A StashDB that knows scenes for some hashes. `shape` lets a test break the reply."""

    def __init__(self, known=None, shape=None, fail_on=()):
        self.known = known or {}
        self.requests, self.shape, self.fail_on = [], shape, set(fail_on)

    def __call__(self, url, *, data=None, headers=None, timeout=20):
        body = json.loads(data)
        groups = body["variables"]["fps"]
        hashes = [g[0]["hash"] for g in groups]
        assert all(g[0]["algorithm"] == "OSHASH" and len(g) == 1 for g in groups)
        self.requests.append(hashes)
        if len(self.requests) in self.fail_on:
            raise TimeoutError("StashDB timed out")
        if self.shape == "errors":
            return {"errors": [{"message": "boom"}]}
        if self.shape == "flat":
            return {"data": {"findScenesBySceneFingerprints": [self.scene(h_) for h_ in hashes if h_ in self.known]}}
        if self.shape == "short":
            return {"data": {"findScenesBySceneFingerprints": [[] for _ in hashes[:-1]]}}
        if self.shape == "null":
            return {"data": {"findScenesBySceneFingerprints": None}}
        return {"data": {"findScenesBySceneFingerprints": [[self.scene(h_)] if h_ in self.known else [] for h_ in hashes]}}

    def scene(self, h_):
        return {"id": f"scene-{h_}", "title": f"Title {h_[-4:]}", "release_date": "2025-09-28", "studio": {"id": "s", "name": "Brazzers"},
                "performers": [{"performer": {"id": "p", "name": "Ava Stone", "gender": "FEMALE"}}], "tags": [{"name": "Office"}], "fingerprints": []}


@pytest.fixture
def stash(monkeypatch):
    fake = FakeStash()
    monkeypatch.setattr(prov, "_http", fake)
    return fake


def many(db, hashes, **kw):
    return h.run(prov.stashdb_by_oshash_many(db, "key", hashes, **kw))


def test_a_hundred_files_cost_three_requests_not_a_hundred(stash):
    db = h.fresh_db()
    hashes = [hexhash(0xAA0000 + i) for i in range(100)]
    out = many(db, hashes)
    assert [len(r) for r in stash.requests] == [40, 40, 20] and len(out) == 100
    assert [x for r in stash.requests for x in r] == hashes                     # in order, each once


def test_each_answer_belongs_to_the_file_it_was_asked_about(stash):
    db = h.fresh_db()
    hashes = [hexhash(0xBB0000 + i) for i in range(6)]
    stash.known = {hashes[1]: True, hashes[4]: True}
    out = many(db, hashes)
    assert [bool(out[x]) for x in hashes] == [False, True, False, False, True, False]
    assert out[hashes[1]][0]["id"] == f"scene-{hashes[1]}" and out[hashes[4]][0]["title"] == f"Title {hashes[4][-4:]}"
    assert out[hashes[1]][0]["performers"] == ["Ava Stone"] and out[hashes[1]][0]["studio"] == "Brazzers" and out[hashes[1]][0]["source"] == "stashdb"


def test_answers_are_cached_so_nothing_is_asked_twice_and_the_single_lookup_reuses_them(stash):
    db = h.fresh_db()
    hashes = [hexhash(0xCC0000 + i) for i in range(5)]
    stash.known = {hashes[0]: True}
    many(db, hashes)
    assert len(stash.requests) == 1
    many(db, hashes)
    assert len(stash.requests) == 1                                           # all cached, including the no-match ones
    one = h.run(prov.stashdb_by_oshash(db, "key", hashes[0]))
    none = h.run(prov.stashdb_by_oshash(db, "key", hashes[3]))
    assert len(stash.requests) == 1 and one[0]["id"] == f"scene-{hashes[0]}" and none == []   # the per-video path sends nothing


def test_only_the_hashes_not_cached_are_sent(stash):
    db = h.fresh_db()
    first = [hexhash(0xDD0000 + i) for i in range(3)]
    many(db, first)
    more = first + [hexhash(0xDD1000 + i) for i in range(2)]
    stash.requests.clear()
    many(db, more)
    assert stash.requests == [more[3:]]


def test_invalid_and_repeated_hashes_are_dropped_and_no_hashes_means_no_request(stash):
    db = h.fresh_db()
    good = hexhash(0xEE0001)
    out = many(db, [good, good.upper(), "", "xyz", "123", None, good])
    assert list(out) == [good] and stash.requests == [[good]]
    stash.requests.clear()
    assert many(db, []) == {} and stash.requests == []


@pytest.mark.parametrize("shape", ["flat", "short", "null", "errors"])
def test_a_reply_that_is_not_one_answer_per_file_is_never_trusted(stash, shape):
    db = h.fresh_db()
    hashes = [hexhash(0xF00000 + i + ({"flat": 1, "short": 2, "null": 3, "errors": 4}[shape] << 12)) for i in range(4)]
    stash.known = {hashes[0]: True, hashes[2]: True}
    stash.shape = shape
    assert many(db, hashes) == {}                                              # nothing guessed, nothing cached
    stash.shape = None
    stash.requests.clear()
    assert len(h.run(prov.stashdb_by_oshash(db, "key", hashes[0]))) == 1       # the normal per-video lookup still works
    assert len(stash.requests) == 1


def test_one_failed_request_does_not_stop_the_others(stash):
    db = h.fresh_db()
    hashes = [hexhash(0x990000 + i) for i in range(90)]
    stash.fail_on = {2}
    out = many(db, hashes)
    assert len(stash.requests) == 3 and set(out) == set(hashes[:40]) | set(hashes[80:])      # the middle 40 are left for the per-video path


def test_the_batch_size_is_limited(stash):
    db = h.fresh_db()
    hashes = [hexhash(0x880000 + i) for i in range(150)]
    many(db, hashes, batch_size=10_000)
    assert max(len(r) for r in stash.requests) == 100                          # a request never carries more than 100
