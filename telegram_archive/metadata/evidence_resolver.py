"""One decision point for conflicting identification evidence about a provider scene candidate.

A candidate scene (from StashDB / ThePornDB) is weighed against what the video's own snapshots say: the studio whose watermark is on it and the
performers whose faces are in it. Every signal either agrees with the candidate or contradicts it, and the rules are deliberately one-sided:

* a hash match (OSHASH) is authoritative and is never downgraded, only annotated;
* one contradiction caps the candidate at MEDIUM (a person reviews it), two or more drop it to LOW;
* a candidate is promoted from MEDIUM to HIGH only when the watermark AND a face (two independent image signals) both agree with it, nothing
  contradicts it, and the matcher's own score is already solid;
* snapshot evidence alone never applies anything: it can only confirm or hold back a provider candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re

from ..core.swallowed import note as _swallowed_note

PROMOTE_MIN_SCORE = 0.60        # the matcher's own score a MEDIUM candidate needs before two agreeing image signals can promote it
STUDIO_SAME = 0.88              # name similarity at which two studio names are the same studio
PERSON_SAME = 0.90


@dataclass
class Signal:
    kind: str                   # "studio" | "performer"
    name: str
    source: str = "snapshot"
    score: float = 0.0


@dataclass
class Verdict:
    confidence: str
    reason: str = ""
    agree: list[str] = field(default_factory=list)
    conflict: list[str] = field(default_factory=list)
    promoted: bool = False
    capped: bool = False


def _key(value: object) -> str:
    return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _same(a: object, b: object, threshold: float) -> bool:
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return False
    return ka == kb or SequenceMatcher(None, ka, kb).ratio() >= threshold


def candidate_cast(best: dict) -> list[str]:
    names: list[str] = []
    for item in best.get("performer_details") or best.get("performers") or []:
        if isinstance(item, dict):
            names.append(str(item.get("name") or ""))
            aliases = item.get("aliases") or []
            names.extend([str(a) for a in ([aliases] if isinstance(aliases, str) else aliases)])
        else:
            names.append(str(item or ""))
    return [n for n in dict.fromkeys(n.strip() for n in names) if n]


def candidate_studios(best: dict) -> list[str]:
    names: list[str] = []
    for field_name in ("studio", "site", "parent_studio", "parent_network", "network", "studio_parent", "studio_network"):
        value = best.get(field_name)
        if isinstance(value, dict):
            value = value.get("name")
        names.append(str(value or ""))
    for field_name in ("studio_aliases", "parent_studio_aliases", "parent_network_aliases", "studio_parent_aliases"):
        value = best.get(field_name) or []
        names.extend([str(v) for v in ([value] if isinstance(value, str) else value)])
    return [n for n in dict.fromkeys(n.strip() for n in names) if n]


def judge(best: dict, signals: list[Signal], *, confidence: str, method: str | None = None, score: float | None = None) -> Verdict:
    """Weigh the signals against one candidate. Pure: no database, no network."""
    verdict = Verdict(confidence=confidence)
    studios, cast = candidate_studios(best), candidate_cast(best)
    studio_signals = [s for s in signals if s.kind == "studio"]
    person_signals = [s for s in signals if s.kind == "performer"]

    studio_agrees = studio_conflicts = False
    if studio_signals and studios:
        if any(_same(s.name, c, STUDIO_SAME) for s in studio_signals for c in studios):
            studio_agrees = True
            verdict.agree.append(f"watermark studio {studio_signals[0].name}")
        else:
            studio_conflicts = True
            verdict.conflict.append(f"watermark says {studio_signals[0].name}, the scene is from {studios[0]}")

    people_agree = people_conflict = False
    if person_signals and cast:
        hit = [s for s in person_signals if any(_same(s.name, c, PERSON_SAME) for c in cast)]
        if hit:
            people_agree = True
            verdict.agree.append("face match " + ", ".join(s.name for s in hit[:3]))
        else:
            people_conflict = True
            verdict.conflict.append("the faces are " + ", ".join(s.name for s in person_signals[:3]) + ", who are not in the scene's cast")

    if str(method or "").endswith("oshash"):
        if verdict.conflict:
            verdict.reason = "exact file fingerprint match kept; snapshots disagree: " + "; ".join(verdict.conflict)
        return verdict                                   # authoritative: annotated, never changed

    if len(verdict.conflict) >= 2:
        if verdict.confidence in ("HIGH", "MEDIUM"):
            verdict.confidence, verdict.capped = "LOW", True
        verdict.reason = "snapshots contradict this scene: " + "; ".join(verdict.conflict)
    elif verdict.conflict:
        if verdict.confidence == "HIGH":
            verdict.confidence, verdict.capped = "MEDIUM", True
        verdict.reason = "snapshots disagree, confirmation needed: " + verdict.conflict[0]
    elif studio_agrees and people_agree and verdict.confidence == "MEDIUM" and float(score or 0.0) >= PROMOTE_MIN_SCORE and str(method or "") != "reference":
        verdict.confidence, verdict.promoted = "HIGH", True
        verdict.reason = "provider candidate confirmed by the snapshots: " + "; ".join(verdict.agree)
    elif verdict.agree:
        verdict.reason = "snapshots agree: " + "; ".join(verdict.agree)
    return verdict


def snapshot_signals(db, media_id: int, *, compute: bool = True) -> list[Signal]:
    """The studio / performers the video's snapshots point to (suggestions that were not rejected). When the video has not been looked at yet
    and `compute` is true, its snapshots are read now."""
    from ..engines import snapshot_identity as si
    if compute:
        with db.lock:
            looked = db.conn.execute("SELECT 1 FROM video_snapshot_looks WHERE video_id=?", (int(media_id),)).fetchone()
        if not looked:
            si.identify_video(db, int(media_id), extract=si.shared_extractor())
    with db.lock:
        rows = db.conn.execute(
            "SELECT q.kind,q.score,COALESCE(p.name,s.name,'') AS name FROM video_snapshot_suggestions q "
            "LEFT JOIN performers p ON q.kind='performer' AND p.id=q.entity_id LEFT JOIN studios s ON q.kind='studio' AND s.id=q.entity_id "
            "WHERE q.video_id=? AND q.status IN ('PENDING','CONFIRMED') ORDER BY q.score DESC", (int(media_id),)).fetchall()
    return [Signal(kind=r["kind"], name=r["name"], score=float(r["score"])) for r in rows if str(r["name"] or "").strip()]


def resolve_for_video(db, channel_id, message_id, best: dict, *, confidence: str, method: str | None, score: float | None) -> Verdict:
    """judge() with the snapshot evidence of this video. Any problem here leaves the confidence as it was."""
    try:
        with db.lock:
            row = db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", (str(channel_id), int(message_id))).fetchone()
        if not row:
            return Verdict(confidence=confidence)
        signals = snapshot_signals(db, int(row[0]))
        return judge(best, signals, confidence=confidence, method=method, score=score) if signals else Verdict(confidence=confidence)
    except Exception:
        _swallowed_note("evidence_resolver.resolve_for_video")
        return Verdict(confidence=confidence)
