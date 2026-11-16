"""Pictures of a performer taken from the snapshots of the videos mapped to them.

The face of a performer in their own videos is the best material there is for recognising them: same lighting, same look as the rest of the
library. For each video mapped to a performer the faces in its 36 snapshots are read, the sharp, clear ones that are this performer become
gallery pictures (source "snapshot"), and the gallery already feeds the People check, so recognition improves with every video.

Which face is theirs:
* when the performer already has reference faces (provider pictures, confirmed faces), a face counts when it matches them (and clearly
  better than any other performer);
* with no references yet, only a video where they are the only mapped performer can start them off, and only through the one face that
  appears in many of its snapshots.
A picture you remove stays removed (it is never taken again), and nothing here changes the profile picture: that is one click in the Gallery.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from .. import config
from ..core.swallowed import note as _swallowed_note
from . import performer_gallery as pg
from . import snapshot_identity as si

MAX_SNAPSHOT_PICTURES = 12      # per performer
PER_VIDEO = 2                   # pictures taken from one video
MIN_FACE_PX = 56                # the face must be at least this wide in the snapshot (smaller makes a poor picture)
MIN_CONFIDENCE = 0.85           # detector confidence
REJECTED_LIKE = 0.60            # a face this similar to one the person removed is not taken (it is the same wrong person)
ACCEPT = 0.46                   # similarity to the performer's references (the automatic threshold used for photos)
MARGIN = 0.05                   # ... and by how much it beats every other performer
BOOT_FRAMES = 6                 # with no references: the dominant face must be in this many snapshots
BOOT_LINK = 0.50                # faces this similar are the same person when looking for the dominant face
DUPLICATE = 0.93                # a face this similar to one already kept adds nothing
CROP_SCALE = 2.2                # the portrait is this many face-widths wide


def _decode(raw) -> list[float] | None:
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) and len(v) == 128 else None
    except Exception:
        return None


def dominant_face(per_frame: list[list[dict]]) -> list[dict]:
    """The faces (one dict per frame at most) of the person who appears in the most snapshots, or [] when nobody does BOOT_FRAMES times."""
    import numpy as np
    flat = [(i, f, np.asarray(f["embedding"], dtype=np.float32)) for i, faces in enumerate(per_frame) for f in faces]
    best: list[tuple[int, dict]] = []
    for _i, _f, anchor in flat:
        members: dict[int, tuple[float, dict]] = {}
        for i, f, v in flat:
            score = float(anchor @ v)
            if score >= BOOT_LINK and (i not in members or score > members[i][0]):
                members[i] = (score, f)
        if len(members) > len(best):
            best = [(i, m[1]) for i, m in members.items()]
    return [f for _i, f in best] if len(best) >= BOOT_FRAMES else []


def own_faces(per_frame: list[list[dict]], pid: int, ids: list[int], matrix) -> list[dict]:
    """Faces that match performer `pid`'s references and beat every other performer by MARGIN."""
    import numpy as np
    out: list[dict] = []
    for faces in per_frame:
        for f in faces:
            v = np.asarray(f["embedding"], dtype=np.float32)
            people, scores = pg.best_per_performer(ids, matrix @ v, top=1)
            by = dict(zip(people, scores))
            mine = by.get(pid, -1.0)
            others = max((s for p, s in by.items() if p != pid), default=-1.0)
            if mine >= ACCEPT and mine - others >= MARGIN:
                out.append(f)
    return out


def _sharp_and_clear(face: dict) -> bool:
    try:
        return float(face["bbox"][2]) >= MIN_FACE_PX and float(face.get("confidence") or 0.0) >= MIN_CONFIDENCE
    except Exception:
        return False


def _portrait(path: str, bbox: list[float]) -> tuple[bytes, int, int] | None:
    """A square portrait around the face, cut from the full-colour snapshot."""
    import cv2
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    x, y, bw, bh = [float(v) for v in bbox]
    side = max(bw, bh) * CROP_SCALE
    cx, cy = x + bw / 2, y + bh / 2 - bh * 0.05
    half = min(side / 2, w / 2, h / 2)
    x0, y0 = int(round(min(max(cx - half, 0), w - 2 * half))), int(round(min(max(cy - half, 0), h - 2 * half)))
    crop = img[y0:y0 + int(2 * half), x0:x0 + int(2 * half)]
    if crop.size == 0:
        return None
    ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return (bytes(buf), int(crop.shape[1]), int(crop.shape[0])) if ok else None


def _mapped_videos(db, pid: int) -> list[int]:
    with db.lock:
        return [int(r[0]) for r in db.conn.execute(
            "SELECT video_id FROM video_performers WHERE performer_id=? UNION SELECT video_id FROM local_video_performers WHERE performer_id=? ORDER BY 1 DESC",
            (pid, pid)).fetchall()]


def grow_gallery(db, performer_id: int, *, reader: Callable[[str], list[dict]], max_videos: int = 6) -> dict:
    """Look through up to `max_videos` of the performer's not-yet-searched videos and keep their best faces as gallery pictures."""
    pg.ensure_tables(db)
    pid = int(performer_id)
    out = {"performer_id": pid, "videos": 0, "added": 0}
    with db.lock:
        name = db.conn.execute("SELECT name FROM performers WHERE id=?", (pid,)).fetchone()
        if not name or str(name[0] or "").strip().casefold() == "unknown performer":
            return out
        have = int(db.conn.execute("SELECT COUNT(*) FROM performer_gallery WHERE performer_id=? AND status='ACTIVE' AND source='snapshot'", (pid,)).fetchone()[0])
        done = {int(r[0]): int(r[1]) for r in db.conn.execute("SELECT video_id,own_refs FROM performer_video_faces_done WHERE performer_id=?", (pid,)).fetchall()}
        kept = [v for v in (_decode(r[0]) for r in db.conn.execute(
            "SELECT embedding_json FROM performer_gallery WHERE performer_id=? AND status='ACTIVE' AND embedding_json<>'[]'", (pid,)).fetchall()) if v]
        removed = [v for v in (_decode(r[0]) for r in db.conn.execute(
            "SELECT embedding_json FROM performer_gallery WHERE performer_id=? AND status='REJECTED' AND source='snapshot' AND embedding_json<>'[]'", (pid,)).fetchall()) if v]
    if have >= MAX_SNAPSHOT_PICTURES:
        return out
    import numpy as np
    ids, matrix = si._references(db)
    own_refs = sum(1 for i in ids if i == pid)
    kept_vecs = [np.asarray(v, dtype=np.float32) for v in kept]
    removed_vecs = [np.asarray(v, dtype=np.float32) for v in removed]
    todo = [v for v in _mapped_videos(db, pid) if v not in done or (done[v] == 0 and own_refs > 0)]
    for vid in todo:
        if have >= MAX_SNAPSHOT_PICTURES or out["videos"] >= max_videos:
            break
        with db.lock:
            ref = si.video_ref(db.conn, vid)
            people = int(db.conn.execute("SELECT COUNT(*) FROM (SELECT performer_id FROM video_performers WHERE video_id=? UNION SELECT performer_id FROM local_video_performers WHERE video_id=?)", (vid, vid)).fetchone()[0])
        files = si.frame_files(*ref) if ref else []
        if len(files) < si.MIN_FRAMES:
            continue                                     # no snapshots yet: looked at when they exist
        per_frame, failed = [], False
        for path in files:
            try:
                faces = reader(str(path))
            except Exception:
                _swallowed_note("performer_snapshot_faces.grow_gallery.read")
                faces, failed = [], True
            per_frame.append([dict(f, _path=str(path)) for f in faces])
        if failed:
            continue                                     # try the whole video again another time
        if own_refs and matrix is not None:
            mine = own_faces(per_frame, pid, ids, matrix)
        elif people == 1:
            mine = dominant_face(per_frame)
        else:
            mine = []
        added = 0
        for f in sorted((f for f in mine if _sharp_and_clear(f)), key=lambda f: -(float(f["bbox"][2]) * float(f["bbox"][3]) * float(f.get("confidence") or 1.0))):
            if added >= PER_VIDEO or have >= MAX_SNAPSHOT_PICTURES:
                break
            v = np.asarray(f["embedding"], dtype=np.float32)
            if any(float(v @ k) >= DUPLICATE for k in kept_vecs) or any(float(v @ k) >= REJECTED_LIKE for k in removed_vecs):
                continue
            shot = _portrait(f["_path"], f["bbox"])
            if not shot:
                continue
            data, width, height = shot
            sha = hashlib.sha256(data).hexdigest()
            key = "snapshot:" + sha[:40]
            folder = pg.gallery_dir() / str(pid)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"snap-{sha[:24]}.jpg"
            temp = target.with_suffix(".part")
            temp.write_bytes(data)
            temp.replace(target)
            now = config.now_iso()
            with db.lock:
                cur = db.conn.execute(
                    "INSERT OR IGNORE INTO performer_gallery(performer_id,url_hash,url,provider,provider_id,file_path,sha256,width,height,bytes,status,faces,embedding_json,source,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,'ACTIVE',1,?,'snapshot',?,?)",
                    (pid, key, f"video:{vid}", "snapshot", str(vid), f"{pid}/{target.name}", sha, width, height, len(data), json.dumps(list(map(float, v))), now, now))
                db.conn.commit()
                inserted = cur.rowcount > 0
            if not inserted:
                _unlink_quiet(target)                    # removed earlier (REJECTED rows keep their key) or already there
                continue
            kept_vecs.append(v)
            have += 1
            added += 1
        with db.lock:
            db.conn.execute("INSERT OR REPLACE INTO performer_video_faces_done(performer_id,video_id,own_refs,added,updated_at) VALUES (?,?,?,?,?)",
                            (pid, vid, own_refs, added, config.now_iso()))
            db.conn.commit()
        out["videos"] += 1
        out["added"] += added
    return out


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _swallowed_note("performer_snapshot_faces._unlink_quiet")


def grow_all(db, *, reader: Callable[[str], list[dict]], max_videos: int = 12) -> dict:
    """grow_gallery across performers (those with the fewest snapshot pictures first), at most `max_videos` videos in all."""
    with db.lock:
        rows = db.conn.execute(
            "SELECT p.id FROM performers p WHERE LOWER(p.name)<>'unknown performer' AND (EXISTS (SELECT 1 FROM video_performers v WHERE v.performer_id=p.id) "
            "OR EXISTS (SELECT 1 FROM local_video_performers l WHERE l.performer_id=p.id)) "
            "AND (SELECT COUNT(*) FROM performer_gallery g WHERE g.performer_id=p.id AND g.status='ACTIVE' AND g.source='snapshot')<? "
            "ORDER BY (SELECT COUNT(*) FROM performer_gallery g WHERE g.performer_id=p.id AND g.status='ACTIVE' AND g.source='snapshot'), p.id", (MAX_SNAPSHOT_PICTURES,)).fetchall()
    total = {"performers": 0, "videos": 0, "added": 0}
    budget = int(max_videos)
    for r in rows:
        if budget <= 0:
            break
        res = grow_gallery(db, int(r[0]), reader=reader, max_videos=budget)
        budget -= res["videos"]
        total["videos"] += res["videos"]
        total["added"] += res["added"]
        total["performers"] += 1 if res["videos"] else 0
    return total
