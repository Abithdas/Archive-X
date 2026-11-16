"""Identify a video's performer and studio from its own 36 snapshots, for videos the providers could not name.

Two signals, both only ever *suggestions* that a person confirms (Quick edit applies them), and both learn from what is confirmed:

* **Studio watermark.** A watermark stays put while the picture behind it changes. Taking the median of the 36 frames inside each corner
  keeps the static overlay and blurs the moving content away. The studio's mark is learned from the videos that already have that studio
  (a mark counts only when most of the studio's videos agree on it), then an unnamed video whose corner mark matches one studio clearly
  better than any other gets that studio suggested.
* **Performer faces.** The faces in the snapshots are compared with every performer's references (profile picture, gallery pictures, faces
  you confirmed). A performer is suggested when their face is the clear best match in several frames.

Nothing here downloads a model or contacts a provider: the face reader is passed in (``make_extractor`` builds the real one from the
face-analysis models), so everything is testable with generated frames.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..core.swallowed import note as _swallowed_note

CORNERS = ("tl", "tr", "bl", "br")
REGION = 0.28                   # each corner region is this fraction of the frame's width and height
SIDE = 48                       # a corner is reduced to SIDE x SIDE before comparing
MIN_FRAMES = 12                 # fewer snapshots than this cannot separate a static mark from the picture
PRESENT_MIN = 0.012             # share of a corner's pixels that must be a static edge before it counts as holding a mark
MARK_MATCH = 0.88               # cosine similarity needed between a video's corner and a studio's learned mark
MARK_MARGIN = 0.10              # ... and by how much it must beat the next studio
MARK_AGREE = 0.85               # videos of one studio agree on a mark when this similar to the typical one
MARK_MIN_VIDEOS = 3             # a studio's mark needs at least this many agreeing videos ...
MARK_MIN_SHARE = 0.5            # ... and at least this share of the videos looked at
FACE_MIN_SCORE = 0.40           # SFace cosine for a face to vote for a performer (the suggestion threshold used for photos)
FACE_MARGIN = 0.04
FACE_MIN_FRAMES = 3             # a performer is suggested when they win in at least this many frames ...
FACE_MIN_MEAN = 0.42            # ... with at least this mean score
FACE_KEEP = 6                   # matched faces kept (as references) when a suggestion is confirmed


# ---------------------------------------------------------------- the watermark ---------------------------------------------------------------
def corner_stack(frames: list, corner: str):
    """(n, SIDE, SIDE) float32: the same corner of every frame, reduced. `frames` are grayscale arrays of equal size."""
    import cv2
    import numpy as np
    out = []
    for gray in frames:
        h, w = gray.shape[:2]
        ch, cw = max(8, int(h * REGION)), max(8, int(w * REGION))
        y0 = 0 if corner[0] == "t" else h - ch
        x0 = 0 if corner[1] == "l" else w - cw
        out.append(cv2.resize(gray[y0:y0 + ch, x0:x0 + cw].astype(np.float32), (SIDE, SIDE), interpolation=cv2.INTER_AREA))
    return np.stack(out)


def mark_signature(stack) -> tuple[list[float] | None, float]:
    """(signature, strength) of the static content of a corner. `strength` is the share of the corner that is a static edge: a corner
    without a watermark has almost none. The signature is the static part's local contrast, a unit vector (None when there is nothing)."""
    import cv2
    import numpy as np
    median = np.median(stack, axis=0).astype(np.float32)
    spread = stack.std(axis=0).astype(np.float32)
    # Static means "moves clearly less than the picture around it": a semi-transparent mark still shifts with the picture behind it, but
    # by (1 - opacity) of it. When nothing moves at all (a bar, a still image) a small fixed floor decides.
    static = spread < max(0.75 * float(np.median(spread)), 6.0)
    smooth = cv2.GaussianBlur(median, (3, 3), 0)
    edge = np.hypot(cv2.Sobel(smooth, cv2.CV_32F, 1, 0), cv2.Sobel(smooth, cv2.CV_32F, 0, 1))
    strength = float(((edge > 40.0) & static).mean())
    contrast = (median - cv2.GaussianBlur(median, (0, 0), 6.0)) * static
    contrast = cv2.GaussianBlur(contrast, (0, 0), 1.2).reshape(-1)
    contrast = contrast - contrast.mean()
    norm = float(np.linalg.norm(contrast))
    if not norm or not np.isfinite(norm):
        return None, strength
    return (contrast / norm).astype(float).tolist(), strength


def video_marks(frames: list) -> dict[str, tuple[list[float], float]]:
    """corner -> (signature, strength) for the corners of this video that hold a mark."""
    out: dict[str, tuple[list[float], float]] = {}
    if len(frames) < MIN_FRAMES:
        return out
    for corner in CORNERS:
        signature, strength = mark_signature(corner_stack(frames, corner))
        if signature is not None and strength >= PRESENT_MIN:
            out[corner] = (signature, strength)
    return out


def typical_mark(vectors: list) -> tuple[list[float] | None, int]:
    """The mark most of `vectors` agree on and how many agree. The most typical vector is found first (highest mean similarity to the rest);
    those within MARK_AGREE of it are averaged. A studio whose videos disagree gets no mark."""
    import numpy as np
    if not vectors:
        return None, 0
    matrix = np.asarray(vectors, dtype=np.float32)
    sims = matrix @ matrix.T
    centre = int(np.argmax(sims.mean(axis=1)))
    members = np.where(sims[centre] >= MARK_AGREE)[0]
    mean = matrix[members].mean(axis=0)
    norm = float(np.linalg.norm(mean))
    return ((mean / norm).astype(float).tolist() if norm else None), int(len(members))


def match_studio(video_marks_: dict, references: dict[int, dict[str, list[float]]]) -> list[dict]:
    """Studios whose learned mark matches a corner of this video, best first: [{studio_id, corner, score, margin}]. A studio is only
    returned when it beats every other studio on that corner by MARK_MARGIN, so a mark two studios share is never guessed."""
    import numpy as np
    found: dict[int, dict] = {}
    for corner, (signature, _strength) in video_marks_.items():
        target = np.asarray(signature, dtype=np.float32)
        scored = sorted(((float(target @ np.asarray(marks[corner], dtype=np.float32)), sid) for sid, marks in references.items() if corner in marks), reverse=True)
        if not scored or scored[0][0] < MARK_MATCH:
            continue
        margin = scored[0][0] - (scored[1][0] if len(scored) > 1 else 0.0)
        if margin < MARK_MARGIN:
            continue
        sid = scored[0][1]
        if sid not in found or scored[0][0] > found[sid]["score"]:
            found[sid] = {"studio_id": sid, "corner": corner, "score": round(scored[0][0], 4), "margin": round(margin, 4)}
    return sorted(found.values(), key=lambda m: -m["score"])


# ---------------------------------------------------------------- faces -------------------------------------------------------------------------
def face_votes(per_frame_faces: list[list[list[float]]], ref_ids: list[int], ref_matrix) -> dict[int, dict]:
    """performer -> {frames, mean, best, faces} from the faces found in each frame. A face votes for the performer whose best reference it
    matches (at least FACE_MIN_SCORE, and FACE_MARGIN better than the runner-up performer). `faces` are the embeddings that voted."""
    import numpy as np
    from .performer_gallery import best_per_performer
    votes: dict[int, dict] = {}
    if ref_matrix is None or not len(ref_ids):
        return votes
    for frame_faces in per_frame_faces:
        seen: set[int] = set()
        for embedding in frame_faces:
            v = np.asarray(embedding, dtype=np.float32)
            n = float(np.linalg.norm(v))
            if v.shape != (128,) or not n or not np.isfinite(n):
                continue
            v = v / n
            people, scores = best_per_performer(ref_ids, ref_matrix @ v, top=1)
            order = np.argsort(-np.asarray(scores))
            best = float(scores[order[0]])
            second = float(scores[order[1]]) if len(order) > 1 else -1.0
            if best < FACE_MIN_SCORE or best - second < FACE_MARGIN:
                continue
            pid = int(people[int(order[0])])
            entry = votes.setdefault(pid, {"frames": 0, "scores": [], "faces": []})
            if pid not in seen:
                entry["frames"] += 1
                seen.add(pid)
            entry["scores"].append(best)
            entry["faces"].append((best, v.astype(float).tolist()))
    out: dict[int, dict] = {}
    for pid, entry in votes.items():
        top = sorted(entry["faces"], key=lambda x: -x[0])[:FACE_KEEP]
        out[pid] = {"frames": entry["frames"], "mean": sum(entry["scores"]) / len(entry["scores"]), "best": max(entry["scores"]), "faces": [f for _s, f in top]}
    return out


def enough_for_a_suggestion(vote: dict) -> bool:
    return vote["frames"] >= FACE_MIN_FRAMES and vote["mean"] >= FACE_MIN_MEAN


# ---------------------------------------------------------------- frames -----------------------------------------------------------------------
FRAME_WIDTH = 320               # snapshots are compared at this width: enough for a corner mark, cheap to read 36 of


def video_ref(conn, media_id: int) -> tuple[int, int] | None:
    row = conn.execute("SELECT source_channel,source_message_id FROM media WHERE id=?", (int(media_id),)).fetchone()
    try:
        return (int(row[0]), int(row[1])) if row and int(row[1] or 0) > 0 else None
    except (TypeError, ValueError):
        return None


def frame_files(channel_id: int, message_id: int) -> list[Path]:
    from .preview_prefetch import preview_frames_path
    root = preview_frames_path(channel_id, message_id)
    try:
        return sorted(p for p in root.glob("frame_*.jpg") if p.is_file())
    except OSError:
        return []


def gray_frames(files: list[Path]) -> list:
    """The snapshots as same-sized grayscale arrays (frames that cannot be read, or are another size, are skipped)."""
    import cv2
    out: list = []
    size = None
    for path in files:
        try:
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        except Exception:
            _swallowed_note("snapshot_identity.gray_frames")
            continue
        if img is None or img.size == 0:
            continue
        h, w = img.shape[:2]
        if size is None:
            size = (FRAME_WIDTH, max(8, int(round(h * FRAME_WIDTH / max(w, 1)))))
        out.append(cv2.resize(img, size, interpolation=cv2.INTER_AREA))
    return out


# ---------------------------------------------------------------- storage ----------------------------------------------------------------------
def _now() -> str:
    return config.now_iso()



def _models():
    """(detector, recognizer) of the face models Archive X already uses for photos. Raises when they are not on this PC yet: a metadata run
    must not start a download by itself, so they are only used once the People feature has fetched them."""
    from . import photo_face_clustering as pfc
    root = pfc._cache_root() / "models"
    yunet, sface = root / "face_detection_yunet_2023mar.onnx", root / "face_recognition_sface_2021dec.onnx"
    if not (yunet.is_file() and sface.is_file()):
        raise RuntimeError("The face models are not installed yet")
    return pfc._new_models(yunet, sface)


def make_extractor() -> Callable[[str], list[list[float]]]:
    """image path -> face embeddings."""
    from . import photo_face_clustering as pfc
    detector, recognizer = _models()

    def extract(path: str) -> list[list[float]]:
        with tempfile.TemporaryDirectory(prefix="ax-snap-faces-") as crops:
            return [f["embedding"] for f in pfc._extract_faces(path, crops, detector, recognizer)]
    return extract


_SHARED: dict = {"fn": None, "tried": 0.0}


def shared_extractor() -> Callable[[str], list[list[float]]] | None:
    """One face reader for the whole process (loading the models takes a moment), or None while they are not installed. A failed attempt is
    not repeated for five minutes."""
    import time
    if _SHARED["fn"] is not None:
        return _SHARED["fn"]
    if time.monotonic() - _SHARED["tried"] < 300 and _SHARED["tried"]:
        return None
    _SHARED["tried"] = time.monotonic()
    try:
        _SHARED["fn"] = make_extractor()
    except Exception:
        _swallowed_note("snapshot_identity.shared_extractor")
        _SHARED["fn"] = None
    return _SHARED["fn"]


def make_face_reader() -> Callable[[str], list[dict]]:
    """image path -> faces as {bbox [x, y, w, h], embedding, confidence} (the aligned crops the detector writes are discarded)."""
    from . import photo_face_clustering as pfc
    detector, recognizer = _models()

    def read(path: str) -> list[dict]:
        with tempfile.TemporaryDirectory(prefix="ax-snap-faces-") as crops:
            return [{"bbox": f["bbox"], "embedding": f["embedding"], "confidence": f.get("confidence", 0.0)}
                    for f in pfc._extract_faces(path, crops, detector, recognizer)]
    return read


def marks_of(db, media_id: int, *, refresh: bool = False) -> dict[str, tuple[list[float], float]] | None:
    """The corner marks of a video, from the cache or its snapshots (None when it has too few). Cached because learning a studio reads
    every one of its videos."""
    with db.lock:
        row = None if refresh else db.conn.execute("SELECT marks_json FROM video_watermark_marks WHERE video_id=?", (int(media_id),)).fetchone()
        ref = video_ref(db.conn, media_id)
    if row is not None:
        try:
            return {k: (v[0], float(v[1])) for k, v in json.loads(row[0]).items()}
        except Exception:
            _swallowed_note("snapshot_identity.marks_of.cache")
    if ref is None:
        return None
    frames = gray_frames(frame_files(*ref))
    if len(frames) < MIN_FRAMES:
        return None
    found = video_marks(frames)
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO video_watermark_marks(video_id,marks_json,updated_at) VALUES (?,?,?)",
                        (int(media_id), json.dumps({k: [v[0], v[1]] for k, v in found.items()}), _now()))
        db.conn.commit()
    return found


def learn_studio_marks(db, *, index_limit: int = 200) -> int:
    """Learn each studio's watermark from its videos (up to `index_limit` newly read per call). Returns studios that now have a mark."""
    with db.lock:
        todo = [int(r[0]) for r in db.conn.execute(
            "SELECT DISTINCT vs.video_id FROM video_studios vs LEFT JOIN video_watermark_marks w ON w.video_id=vs.video_id "
            "WHERE w.video_id IS NULL ORDER BY vs.video_id LIMIT ?", (int(index_limit),)).fetchall()]
    for vid in todo:
        marks_of(db, vid)
    with db.lock:
        rows = db.conn.execute("SELECT vs.studio_id, w.marks_json FROM video_studios vs JOIN video_watermark_marks w ON w.video_id=vs.video_id").fetchall()
    per: dict[int, list[dict]] = {}
    for r in rows:
        try:
            per.setdefault(int(r[0]), []).append(json.loads(r[1]))
        except Exception:
            continue
    learned: dict[int, dict[str, tuple[list[float], int]]] = {}
    for sid, videos in per.items():
        if len(videos) < MARK_MIN_VIDEOS:
            continue
        for corner in CORNERS:
            vectors = [m[corner][0] for m in videos if corner in m]
            if len(vectors) < MARK_MIN_VIDEOS:
                continue
            vec, agree = typical_mark(vectors)
            if vec is not None and agree >= MARK_MIN_VIDEOS and agree / len(videos) >= MARK_MIN_SHARE:
                learned.setdefault(sid, {})[corner] = (vec, agree)
    with db.lock:
        db.conn.execute("DELETE FROM studio_watermarks")
        stamp = _now()
        for sid, corners in learned.items():
            for corner, (vec, agree) in corners.items():
                db.conn.execute("INSERT INTO studio_watermarks(studio_id,corner,vector_json,videos,updated_at) VALUES (?,?,?,?,?)",
                                (sid, corner, json.dumps(vec), agree, stamp))
        db.conn.commit()
    return len(learned)


def studio_references(db) -> dict[int, dict[str, list[float]]]:
    with db.lock:
        rows = db.conn.execute("SELECT studio_id,corner,vector_json FROM studio_watermarks").fetchall()
    out: dict[int, dict[str, list[float]]] = {}
    for r in rows:
        try:
            out.setdefault(int(r[0]), {})[r[1]] = json.loads(r[2])
        except Exception:
            continue
    return out


def _references(db):
    """(ids, matrix) of every performer reference: profile pictures, gallery, confirmed photo faces and faces confirmed from snapshots."""
    import numpy as np
    from .performer_gallery import reference_matrix
    with db.lock:
        ids, matrix = reference_matrix(db.conn)
        rows = db.conn.execute("SELECT performer_id,embedding_json FROM video_face_references").fetchall()
    extra_ids, extra_vecs = [], []
    for r in rows:
        try:
            v = np.asarray(json.loads(r[1]), dtype=np.float32)
        except Exception:
            continue
        n = float(np.linalg.norm(v))
        if v.shape == (128,) and n and np.isfinite(n):
            extra_ids.append(int(r[0]))
            extra_vecs.append(v / n)
    if extra_vecs:
        stack = np.stack(extra_vecs)
        matrix = stack if matrix is None else np.vstack([matrix, stack])
        ids = list(ids) + extra_ids
    return list(ids), matrix


def _store(db, media_id: int, kind: str, entity_id: int, score: float, evidence: dict) -> None:
    """Keep a suggestion; one the person rejected (or confirmed) stays as it is."""
    with db.lock:
        db.conn.execute(
            "INSERT INTO video_snapshot_suggestions(video_id,kind,entity_id,score,evidence_json,status,created_at,updated_at) VALUES (?,?,?,?,?,'PENDING',?,?) "
            "ON CONFLICT(video_id,kind,entity_id) DO UPDATE SET score=excluded.score, evidence_json=excluded.evidence_json, updated_at=excluded.updated_at "
            "WHERE status='PENDING'", (int(media_id), kind, int(entity_id), round(float(score), 4), json.dumps(evidence), _now(), _now()))
        db.conn.commit()


def _mark_looked(db, media_id: int, face_refs: int, studio_marks: int) -> None:
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO video_snapshot_looks(video_id,face_refs,studio_marks,updated_at) VALUES (?,?,?,?)",
                        (int(media_id), int(face_refs), int(studio_marks), _now()))
        db.conn.commit()


def identify_video(db, media_id: int, *, extract: Callable[[str], list[list[float]]] | None, refs=None, studios=None) -> dict:
    """Suggest a studio and performers for one video from its snapshots. Never changes the video itself. Returns what was suggested."""
    result = {"studio": [], "performers": [], "frames": 0}
    with db.lock:
        ref = video_ref(db.conn, media_id)
        linked_studio = db.conn.execute("SELECT 1 FROM video_studios WHERE video_id=? LIMIT 1", (int(media_id),)).fetchone() is not None
        linked_people = {int(r[0]) for r in db.conn.execute(
            "SELECT performer_id FROM video_performers WHERE video_id=? UNION SELECT performer_id FROM local_video_performers WHERE video_id=?",
            (int(media_id), int(media_id))).fetchall()}
    if ref is None:
        return result
    files = frame_files(*ref)
    result["frames"] = len(files)
    if len(files) < MIN_FRAMES:
        return result
    studios = studios if studios is not None else studio_references(db)
    if extract is not None and refs is None and not linked_people:
        refs = _references(db)
    if not linked_studio:
        marks = marks_of(db, media_id)
        for m in match_studio(marks or {}, studios) if studios else []:
            _store(db, media_id, "studio", m["studio_id"], m["score"], {"corner": m["corner"], "margin": m["margin"]})
            result["studio"].append(m)
    if extract is not None and not linked_people:
        ids, matrix = refs
        if matrix is not None and ids:
            per_frame: list[list[list[float]]] = []
            for path in files:
                try:
                    per_frame.append(extract(str(path)))
                except Exception:
                    _swallowed_note("snapshot_identity.identify_video.extract")
                    per_frame.append([])
            for pid, vote in face_votes(per_frame, ids, matrix).items():
                if pid in linked_people or not enough_for_a_suggestion(vote):
                    continue
                _store(db, media_id, "performer", pid, vote["mean"], {"frames": vote["frames"], "best": round(vote["best"], 4), "faces": vote["faces"]})
                result["performers"].append({"performer_id": pid, "frames": vote["frames"], "score": round(vote["mean"], 4)})
    _mark_looked(db, media_id, len(refs[0]) if (refs is not None and extract is not None) else -1, len(studios))
    return result


def unresolved_videos(db, limit: int = 100, *, face_refs: int = -1, studio_marks: int = 0) -> list[int]:
    """Videos with no studio or no performer yet that have not been looked at since the studio marks and face references last changed,
    newest first (so a new gallery picture or a newly learned mark gives them another look, and nothing is read twice for nothing)."""
    with db.lock:
        return [int(r[0]) for r in db.conn.execute(
            "SELECT m.id FROM media m LEFT JOIN video_snapshot_looks k ON k.video_id=m.id WHERE m.source_message_id>0 AND COALESCE(m.status,'')<>'SOURCE_DELETED' "
            "AND (NOT EXISTS (SELECT 1 FROM video_studios s WHERE s.video_id=m.id) OR (NOT EXISTS (SELECT 1 FROM video_performers p WHERE p.video_id=m.id) "
            "AND NOT EXISTS (SELECT 1 FROM local_video_performers l WHERE l.video_id=m.id))) "
            "AND (k.video_id IS NULL OR k.face_refs<>? OR k.studio_marks<>?) ORDER BY m.id DESC LIMIT ?",
            (int(face_refs), int(studio_marks), int(limit))).fetchall()]


def run(db, *, limit: int = 100, extract: Callable[[str], list[list[float]]] | None = None, use_faces: bool = True) -> dict:
    """Learn the studios' marks, then look at up to `limit` unresolved videos. Skips faces quietly when the face models are unavailable."""
    summary = {"studios_learned": 0, "videos": 0, "studio_suggestions": 0, "performer_suggestions": 0, "faces": False}
    summary["studios_learned"] = learn_studio_marks(db)
    if extract is None and use_faces:
        try:
            extract = make_extractor()
        except Exception:
            _swallowed_note("snapshot_identity.run.models")
            extract = None
    summary["faces"] = extract is not None
    studios = studio_references(db)
    refs = _references(db) if extract is not None else None
    for vid in unresolved_videos(db, limit, face_refs=len(refs[0]) if refs is not None else -1, studio_marks=len(studios)):
        out = identify_video(db, vid, extract=extract, refs=refs, studios=studios)
        summary["videos"] += 1
        summary["studio_suggestions"] += len(out["studio"])
        summary["performer_suggestions"] += len(out["performers"])
    return summary


def suggestions(db, media_id: int) -> list[dict]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT q.kind,q.entity_id,q.score,q.evidence_json,q.status,COALESCE(p.name,s.name,'') AS name FROM video_snapshot_suggestions q "
            "LEFT JOIN performers p ON q.kind='performer' AND p.id=q.entity_id LEFT JOIN studios s ON q.kind='studio' AND s.id=q.entity_id "
            "WHERE q.video_id=? AND q.status='PENDING' ORDER BY q.kind,q.score DESC", (int(media_id),)).fetchall()
    out = []
    for r in rows:
        try:
            ev = json.loads(r["evidence_json"])
        except Exception:
            ev = {}
        ev.pop("faces", None)
        out.append({"kind": r["kind"], "id": int(r["entity_id"]), "name": r["name"], "score": float(r["score"]), "evidence": ev})
    return out


def reject(db, media_id: int, kind: str, entity_id: int) -> None:
    """The person said it is not them: remembered so it is never suggested again for this video."""
    with db.lock:
        db.conn.execute(
            "INSERT INTO video_snapshot_suggestions(video_id,kind,entity_id,score,evidence_json,status,created_at,updated_at) VALUES (?,?,?,0,'{}','REJECTED',?,?) "
            "ON CONFLICT(video_id,kind,entity_id) DO UPDATE SET status='REJECTED', updated_at=excluded.updated_at",
            (int(media_id), kind, int(entity_id), _now(), _now()))
        db.conn.commit()


def settle(db, media_id: int, performer_ids: list[int], studio_id: int | None, kinds: tuple[str, ...] = ("performer", "studio")) -> dict:
    """After the person saved the video: suggestions they accepted become CONFIRMED and their faces become references, the rest of the
    pending ones of the kinds they edited (`kinds`) that they did not take become REJECTED (they chose someone else). Saving only a title
    leaves the pending suggestions alone."""
    kept = 0
    with db.lock:
        rows = db.conn.execute("SELECT id,kind,entity_id,evidence_json FROM video_snapshot_suggestions WHERE video_id=? AND status='PENDING'", (int(media_id),)).fetchall()
        for r in rows:
            if r["kind"] not in kinds:
                continue
            taken = (r["kind"] == "performer" and int(r["entity_id"]) in {int(x) for x in performer_ids}) or (r["kind"] == "studio" and studio_id is not None and int(r["entity_id"]) == int(studio_id))
            db.conn.execute("UPDATE video_snapshot_suggestions SET status=?, updated_at=? WHERE id=?", ("CONFIRMED" if taken else "REJECTED", _now(), int(r["id"])))
            if taken and r["kind"] == "performer":
                try:
                    faces = json.loads(r["evidence_json"]).get("faces") or []
                except Exception:
                    faces = []
                for emb in faces[:FACE_KEEP]:
                    db.conn.execute("INSERT INTO video_face_references(performer_id,video_id,embedding_json,created_at) VALUES (?,?,?,?)",
                                    (int(r["entity_id"]), int(media_id), json.dumps(emb), _now()))
                    kept += 1
        db.conn.commit()
    return {"face_references_added": kept}
