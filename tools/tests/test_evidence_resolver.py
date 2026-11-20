"""Conflicting identification evidence: the snapshots against a provider candidate, from the pure rules up to the real pipeline step."""
import asyncio

import pytest

import test_snapshot_identity as base
from telegram_archive.engines import snapshot_identity as si
from telegram_archive.metadata import evidence_resolver as er
from telegram_archive.metadata import pipeline as meta_pipeline

S, P = er.Signal, er.Signal


def scene(studio="Northlight", cast=("Ava Stone", "Mia Frost")):
    return {"title": "A scene", "studio": studio, "performers": list(cast), "provider": "stashdb"}


def wm(name="Northlight"):
    return S("studio", name, score=0.93)


def face(name="Ava Stone"):
    return P("performer", name, score=0.5)


def judge(signals, confidence="MEDIUM", method="tpdb filename parse", score=0.7, best=None):
    return er.judge(best or scene(), signals, confidence=confidence, method=method, score=score)


# ---------------------------------------------------------------- the rules ---------------------------------------------------------------
def test_no_signals_changes_nothing():
    v = judge([], confidence="HIGH")
    assert (v.confidence, v.reason, v.agree, v.conflict) == ("HIGH", "", [], [])


def test_watermark_and_face_both_agreeing_promote_a_solid_medium_candidate():
    v = judge([wm(), face()])
    assert v.confidence == "HIGH" and v.promoted and len(v.agree) == 2


def test_one_agreeing_signal_is_not_enough_to_promote():
    assert judge([wm()]).confidence == "MEDIUM" and judge([face()]).confidence == "MEDIUM"


def test_promotion_needs_a_solid_matcher_score_and_not_a_local_reference():
    assert judge([wm(), face()], score=0.4).confidence == "MEDIUM"
    assert judge([wm(), face()], method="reference").confidence == "MEDIUM"
    assert judge([wm(), face()], confidence="LOW").confidence == "LOW"


def test_a_studio_conflict_caps_a_high_candidate_at_medium():
    v = judge([wm("Other Studio")], confidence="HIGH")
    assert v.confidence == "MEDIUM" and v.capped and "Other Studio" in v.reason


def test_a_face_that_is_not_in_the_cast_caps_it():
    v = judge([face("Someone Else")], confidence="HIGH")
    assert v.confidence == "MEDIUM" and v.capped and "not in the scene's cast" in v.reason


def test_two_contradictions_drop_it_to_low():
    v = judge([wm("Other Studio"), face("Someone Else")], confidence="HIGH")
    assert v.confidence == "LOW" and len(v.conflict) == 2


def test_a_conflict_beats_a_partial_agreement():
    v = judge([wm("Other Studio"), face()])
    assert v.confidence == "MEDIUM" and not v.promoted and v.conflict and v.agree


def test_a_hash_match_is_never_downgraded_only_annotated():
    v = judge([wm("Other Studio"), face("Someone Else")], confidence="HIGH", method="stashdb oshash")
    assert v.confidence == "HIGH" and "fingerprint match kept" in v.reason and len(v.conflict) == 2


def test_one_matching_face_among_several_is_agreement_and_aliases_count():
    best = scene(cast=({"name": "Ava Stone", "aliases": ["Avery S"]},))
    assert judge([face("Avery S"), face("Stranger")], best=best).agree
    assert not judge([face("Avery S"), face("Stranger")], best=best).conflict


def test_network_alias_of_the_same_studio_is_not_a_conflict():
    best = scene()
    best["studio_aliases"] = ["Northlight Network"]
    assert not judge([wm("Northlight Network")], best=best).conflict


def test_a_candidate_without_cast_or_studio_cannot_be_contradicted():
    assert not judge([wm("X"), face("Y")], best={"title": "t"}).conflict


# ---------------------------------------------------------------- stored evidence and the pipeline ----------------------------------------
def make(tmp_path):
    db = base.private_db(tmp_path)
    vid = base.make_video(db, tmp_path, 1)
    with db.lock:
        ch, mid = db.conn.execute("SELECT source_channel,source_message_id FROM media WHERE id=?", (vid,)).fetchone()
    return db, vid, ch, mid


def test_rejected_suggestions_are_not_evidence(tmp_path):
    db, vid, *_ = make(tmp_path)
    s = base.studio(db, "Northlight")
    p = base.performer(db, "Ava Stone")
    si._store(db, vid, "studio", s, 0.9, {})
    si._store(db, vid, "performer", p, 0.5, {})
    si.reject(db, vid, "performer", p)
    assert [(x.kind, x.name) for x in er.snapshot_signals(db, vid, compute=False)] == [("studio", "Northlight")]


def test_resolve_for_video_uses_what_the_snapshots_say_and_survives_problems(tmp_path):
    db, vid, ch, mid = make(tmp_path)
    si._store(db, vid, "studio", base.studio(db, "Other Studio"), 0.9, {})
    v = er.resolve_for_video(db, ch, mid, scene(), confidence="HIGH", method="x", score=0.8)
    assert v.confidence == "MEDIUM"
    assert er.resolve_for_video(db, "-1", 99999, scene(), confidence="HIGH", method="x", score=0.8).confidence == "HIGH"
    assert er.resolve_for_video(object(), ch, mid, scene(), confidence="HIGH", method="x", score=0.8).confidence == "HIGH"


class Applied(Exception):
    pass


def run_pipeline(db, ch, mid, monkeypatch, confidence, method="tpdb filename parse", score=0.8, best=None):
    best = best or scene()
    outcome = {"confidence": confidence, "best": best, "score": score, "method": method, "matches": [best], "reason": "test"}

    async def fake(*a, **k):
        return dict(outcome, best=dict(best))
    applied, saved = [], []
    monkeypatch.setattr(meta_pipeline, "_identify_with_context_compat", fake)
    monkeypatch.setattr(meta_pipeline, "apply_scene_match", lambda *a, **k: applied.append(a) or True)

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(meta_pipeline, "collect_markers_for_message", noop)
    monkeypatch.setattr(meta_pipeline, "_upsert_reference_from_match", lambda *a, **k: None)
    monkeypatch.setattr(meta_pipeline, "save_suggestion", lambda db_, c, m, cand, conf, reason, score=None: saved.append((conf, reason)) or 1)
    out = asyncio.run(meta_pipeline._identify_video_core(db, channel_id=ch, message_id=mid, filename="x.mp4", oshash=None))
    return out, applied, saved


def test_pipeline_a_contradicted_high_candidate_is_not_applied_but_suggested(tmp_path, monkeypatch):
    db, vid, ch, mid = make(tmp_path)
    si._store(db, vid, "studio", base.studio(db, "Other Studio"), 0.9, {})
    out, applied, saved = run_pipeline(db, ch, mid, monkeypatch, "HIGH")
    assert not applied and out["action"] == "suggested" and saved[0][0] == "MEDIUM" and "snapshots disagree" in saved[0][1]


def test_pipeline_watermark_and_face_agreement_promotes_a_medium_candidate(tmp_path, monkeypatch):
    db, vid, ch, mid = make(tmp_path)
    si._store(db, vid, "studio", base.studio(db, "Northlight"), 0.9, {})
    si._store(db, vid, "performer", base.performer(db, "Ava Stone"), 0.5, {})
    out, applied, saved = run_pipeline(db, ch, mid, monkeypatch, "MEDIUM")
    assert applied and out["action"] == "applied" and out["confidence"] == "HIGH"


def test_pipeline_a_hash_match_is_applied_despite_conflicting_snapshots(tmp_path, monkeypatch):
    db, vid, ch, mid = make(tmp_path)
    si._store(db, vid, "studio", base.studio(db, "Other Studio"), 0.9, {})
    out, applied, saved = run_pipeline(db, ch, mid, monkeypatch, "HIGH", method="stashdb oshash")
    assert applied and out["action"] == "applied"


def test_pipeline_without_snapshot_evidence_behaves_as_before(tmp_path, monkeypatch):
    db, vid, ch, mid = make(tmp_path)
    out, applied, saved = run_pipeline(db, ch, mid, monkeypatch, "HIGH")
    assert applied and out["action"] == "applied"
