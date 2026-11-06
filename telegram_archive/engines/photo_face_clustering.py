"""Local image-based face detection and anonymous visual clustering for Photos.

This module never assigns a human identity automatically. It detects faces,
creates local SFace embeddings, groups visually similar detections, and lets a
user explicitly map a group to one of Archive X's performer records.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Paths and models
#   - Detection and photo files
#   - Clustering and persistence
#   - Group maintenance
#   - Job entry point
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .. import config

_YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
_SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
_YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
_SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
# SFace's cosine score is only used to cluster similar visual embeddings. It is
# never compared to named performer profile images or used to name a group.
_GROUP_COSINE_THRESHOLD = 0.42




# ==============================================================================
# SECTION: Paths and models
# Cache paths and model download.
# ==============================================================================

def _now() -> str:
    return config.now_iso()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _cache_root() -> Path:
    return Path(config.CACHE_DIR) / "photo_face_groups"


def _download_checked(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ArchiveX-local-face-grouping/1.0"})
        with urllib.request.urlopen(request, timeout=45) as response, temp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(f"Face model checksum mismatch for {destination.name}; file was not installed")
        temp.replace(destination)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass


def _ensure_models() -> tuple[Path, Path]:
    root = _cache_root() / "models"
    yunet = root / "face_detection_yunet_2023mar.onnx"
    sface = root / "face_recognition_sface_2021dec.onnx"
    for path, url, digest in (
        (yunet, _YUNET_URL, _YUNET_SHA256),
        (sface, _SFACE_URL, _SFACE_SHA256),
    ):
        valid = False
        if path.is_file() and path.stat().st_size > 1024:
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            valid = h.hexdigest() == digest
        if not valid:
            _download_checked(url, path, digest)
    return yunet, sface


def _new_models(yunet_path: Path, sface_path: Path):
    import cv2
    detector_api = getattr(cv2, "FaceDetectorYN", None)
    recognizer_api = getattr(cv2, "FaceRecognizerSF", None)
    if detector_api is None or recognizer_api is None:
        raise RuntimeError("Archive X needs OpenCV 4.10 or newer for local face grouping")
    detector_factory = getattr(detector_api, "create", None) or getattr(cv2, "FaceDetectorYN_create", None)
    recognizer_factory = getattr(recognizer_api, "create", None) or getattr(cv2, "FaceRecognizerSF_create", None)
    if detector_factory is None or recognizer_factory is None:
        raise RuntimeError("This OpenCV build does not include YuNet/SFace support")
    detector = detector_factory(str(yunet_path), "", (320, 320), 0.82, 0.30, 5000)
    recognizer = recognizer_factory(str(sface_path), "")
    return detector, recognizer




# ==============================================================================
# SECTION: Detection and photo files
# Face extraction and photo file resolution.
# ==============================================================================

def _extract_faces(image_path: str, crop_dir_path: str, detector, recognizer) -> list[dict[str, Any]]:
    """Return image-derived embeddings and aligned face crops from one image."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    source_path = Path(image_path)
    crop_dir = Path(crop_dir_path)
    crop_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source:
        source = ImageOps.exif_transpose(source)
        try:
            source.seek(0)
        except Exception:
            pass
        rgb = source.convert("RGB")
        # Bound memory use for very large camera/source scans while preserving
        # enough pixels for detection in an ordinary media-library image.
        if max(rgb.size) > 3200:
            ratio = 3200 / max(rgb.size)
            rgb = rgb.resize((max(1, int(rgb.width * ratio)), max(1, int(rgb.height * ratio))), Image.Resampling.LANCZOS)
        image = np.asarray(rgb)
    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    detector.setInputSize((int(width), int(height)))
    try:
        _ok, detected = detector.detect(bgr)
    except Exception as exc:
        raise RuntimeError(f"Face detector failed: {type(exc).__name__}") from exc
    if detected is None or len(detected) == 0:
        return []
    results: list[dict[str, Any]] = []
    # Largest, best-scoring faces first keeps each photo's groups stable.
    faces = sorted(list(detected), key=lambda row: (float(row[2]) * float(row[3]), float(row[14]) if len(row) > 14 else 0), reverse=True)
    for face_index, face in enumerate(faces[:30]):
        try:
            aligned = recognizer.alignCrop(bgr, face)
            feature = np.asarray(recognizer.feature(aligned), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(feature))
            if not norm or not np.isfinite(norm):
                continue
            feature = feature / norm
            crop_path = crop_dir / f"{source_path.stem}_{face_index}.jpg"
            ok, encoded = cv2.imencode(".jpg", aligned, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if not ok:
                continue
            temp = crop_path.with_suffix(".jpg.part")
            temp.write_bytes(encoded.tobytes())
            temp.replace(crop_path)
            x, y, w, h = [float(value) for value in face[:4]]
            results.append({
                "face_index": face_index,
                "bbox": [round(x, 2), round(y, 2), round(w, 2), round(h, 2)],
                "embedding": feature.astype(float).tolist(),
                "crop_path": str(crop_path),
                "confidence": round(float(face[14]), 4) if len(face) > 14 else 0.0,
            })
        except Exception:
            # A bad face crop should not make the whole photo fail.
            continue
    return results


def _photo_cache_path(channel_id: str, message_id: int) -> Path:
    from .photo_cache import photo_original_path
    return photo_original_path(str(channel_id), int(message_id))


def _stored_photo_path(row) -> Path:
    from .photo_cache import resolve_database_cache_path
    try:
        keys = row.keys()
        value = row["local_path"] if "local_path" in keys else ""
    except Exception:
        value = ""
    stored = resolve_database_cache_path(value)
    if stored is not None and stored.is_file() and stored.stat().st_size > 0:
        return stored
    return _photo_cache_path(str(row["channel_id"]), int(row["message_id"]))


async def _ensure_photo_file(ctx, row) -> Path:
    """Use persisted local files first; cache legacy/unmaterialized rows once."""
    from .photo_cache import (
        cache_telegram_message, inspect_cached_file, record_photo_cache,
    )
    cached = _stored_photo_path(row)
    if cached.is_file() and cached.stat().st_size > 0:
        try:
            keys = row.keys()
            has_path = "local_path" in keys and bool(str(row["local_path"] or "").strip())
        except Exception:
            has_path = False
        if not has_path:
            info = await ctx.blocking(inspect_cached_file, cached, fallback_name=str(row["filename"] or ""))
            await ctx.blocking(record_photo_cache, ctx.db, str(row["channel_id"]), int(row["message_id"]), info)
        return cached

    client = getattr(ctx.telegram, "client", None)
    if client is None or not getattr(ctx.telegram, "connected", False):
        raise RuntimeError("Telegram is disconnected and this photo is not cached locally")
    from .photo_channel_scanner import _is_photo_message
    channel_id = str(row["channel_id"])
    message_id = int(row["message_id"])
    try:
        try:
            entity = await client.get_entity(int(channel_id))
        except (TypeError, ValueError):
            entity = await client.get_entity(channel_id)
        message = await client.get_messages(entity, ids=message_id)
        if not message:
            raise RuntimeError("The indexed Telegram message no longer exists")
        is_photo, filename, _mime = _is_photo_message(message)
        if not is_photo:
            raise RuntimeError("The indexed message is no longer an image")
        info = await cache_telegram_message(client, message, channel_id, message_id)
        info["filename"] = str(row["filename"] or filename or "")
        await ctx.blocking(record_photo_cache, ctx.db, channel_id, message_id, info)
        return Path(info["path"])
    except Exception:
        raise




# ==============================================================================
# SECTION: Clustering and persistence
# Centroids, group inserts, scoring and storage.
# ==============================================================================

def _load_centroids(db):
    """Load exemplars from completed *unassigned* groups only.

    Newly analyzed faces cluster against anonymous examples. Groups previously
    mapped to a named performer are not an automatic assignment destination;
    instead, the API may surface them as suggestions requiring user confirmation.
    We keep several normalized exemplars per anonymous group to handle pose and
    lighting variation without relying on a drifting centroid.
    """
    import numpy as np
    groups: dict[int, list[Any]] = defaultdict(list)
    with db.reading() as rdb:
        rows = rdb.execute(
            "SELECT d.group_id,d.embedding_json FROM photo_face_detections d "
            "JOIN photo_face_analysis a ON a.photo_id=d.photo_id "
            "JOIN photo_face_groups g ON g.id=d.group_id "
            "WHERE a.status='COMPLETE' AND g.performer_id IS NULL ORDER BY d.group_id,d.id DESC"
        ).fetchall()
    for row in rows:
        try:
            vector = np.asarray(json.loads(row["embedding_json"] or "[]"), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            gid = int(row["group_id"])
            if not norm or len(vector) != 128 or not np.isfinite(vector).all():
                continue
            vector /= norm
            # Bound memory per cluster while retaining diverse examples by
            # spreading picks over rows instead of retaining the full library.
            if len(groups[gid]) < 12:
                groups[gid].append(vector.copy())
        except Exception:
            continue
    return groups


def _insert_group(cursor, now: str) -> int:
    cur = cursor.execute(
        "INSERT INTO photo_face_groups(label,performer_id,created_at,updated_at) VALUES('',NULL,?,?)",
        (now, now),
    )
    gid = int(cur.lastrowid)
    cursor.execute("UPDATE photo_face_groups SET label=? WHERE id=?", (f"Face group {gid}", gid))
    return gid


def _cosine_score(vector, exemplars) -> float:
    import numpy as np
    if not exemplars:
        return -1.0
    try:
        matrix = np.asarray(exemplars, dtype=np.float32)
        scores = matrix @ vector
        return float(np.max(scores)) if scores.size else -1.0
    except Exception:
        return -1.0


def _store_photo_faces(db, photo_id: int, faces: list[dict[str, Any]], exemplars: dict[int, list[Any]]) -> None:
    """Cluster detections against completed examples and atomically replace a photo.

    Group creation, detections, and COMPLETE status are one SQLite transaction.
    The in-memory exemplar index is updated only after COMMIT, so a failed write
    cannot poison later decisions in the same analysis job. A single photo is
    never assigned two detections to the same group.
    """
    import numpy as np
    now = _now()
    prepared = []
    for face in faces:
        try:
            vector = np.asarray(face["embedding"], dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if len(vector) != 128 or not norm or not np.isfinite(vector).all():
                continue
            vector = vector / norm
            prepared.append((face, vector))
        except Exception:
            continue

    staged_exemplars: dict[int, list[Any]] = {int(g): list(values) for g, values in exemplars.items()}
    reserved_groups: set[int] = set()
    planned: list[tuple[int, int, str, str, str]] = []
    created_groups: list[int] = []
    with db.lock:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            # Assign each detection to the most similar cluster exemplar. To
            # avoid accidental many-to-one face collapse inside group photos,
            # one group may only be assigned once per image.
            for face, vector in prepared:
                best_group, best_score = None, -1.0
                for gid in sorted(staged_exemplars):
                    if gid in reserved_groups:
                        continue
                    score = _cosine_score(vector, staged_exemplars[gid])
                    if score > best_score:
                        best_group, best_score = gid, score
                if best_group is None or best_score < _GROUP_COSINE_THRESHOLD:
                    best_group = _insert_group(db.conn, now)
                    created_groups.append(best_group)
                    staged_exemplars[best_group] = []
                reserved_groups.add(int(best_group))
                crop_path = str(face.get("crop_path") or "")
                planned.append((int(face["face_index"]), int(best_group),
                                json.dumps(face["bbox"], separators=(",", ":")),
                                json.dumps(vector.astype(float).tolist(), separators=(",", ":")),
                                crop_path))
                # Extend this job's working exemplars, so later photos can join
                # clusters discovered earlier in this same job.
                staged_exemplars.setdefault(int(best_group), []).append(vector.copy())
                if len(staged_exemplars[int(best_group)]) > 12:
                    staged_exemplars[int(best_group)] = staged_exemplars[int(best_group)][-12:]

            db.conn.execute("DELETE FROM photo_face_detections WHERE photo_id=?", (int(photo_id),))
            for face_index, group_id, bbox_json, embedding_json, crop_path in planned:
                db.conn.execute(
                    """INSERT INTO photo_face_detections
                       (photo_id,face_index,group_id,bbox_json,embedding_json,crop_path,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (int(photo_id), face_index, group_id, bbox_json, embedding_json, crop_path, now, now),
                )
            db.conn.execute(
                """INSERT INTO photo_face_analysis(photo_id,status,face_count,error,analyzed_at,updated_at)
                   VALUES(?,'COMPLETE',?,'',?,?) ON CONFLICT(photo_id) DO UPDATE SET
                   status='COMPLETE',face_count=excluded.face_count,error='',analyzed_at=excluded.analyzed_at,updated_at=excluded.updated_at""",
                (int(photo_id), len(planned), now, now),
            )
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise

    # Commit successful: publish the working exemplars back to this job.
    exemplars.clear()
    exemplars.update(staged_exemplars)


def _set_analysis_state(db, photo_id: int, status: str, error: str = "") -> None:
    now = _now()
    with db.lock:
        db.conn.execute(
            """INSERT INTO photo_face_analysis(photo_id,status,face_count,error,analyzed_at,updated_at)
               VALUES(?,?,0,?,NULL,?) ON CONFLICT(photo_id) DO UPDATE SET
               status=excluded.status,error=excluded.error,updated_at=excluded.updated_at""",
            (int(photo_id), str(status), str(error or "")[:1000], now),
        )
        db.conn.commit()




# ==============================================================================
# SECTION: Group maintenance
# Merges and splits face groups.
# ==============================================================================

def _merge_face_groups(db, group_ids: list[int], target_group_id: int | None = None) -> int:
    """Merge selected face clusters into a surviving cluster in one transaction."""
    ids = list(dict.fromkeys(int(v) for v in group_ids if int(v) > 0))
    if len(ids) < 2 or len(ids) > 300:
        raise ValueError("Select between 2 and 300 face groups to merge")
    target = int(target_group_id) if target_group_id else min(ids)
    if target not in ids:
        raise ValueError("Target group must be one of the selected groups")
    now = _now()
    marks = ",".join("?" for _ in ids)
    with db.lock:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            rows = db.conn.execute(f"SELECT id,performer_id FROM photo_face_groups WHERE id IN ({marks})", ids).fetchall()
            if {int(row["id"]) for row in rows} != set(ids):
                raise ValueError("One or more selected face groups no longer exist")
            assigned = {int(row["performer_id"]) for row in rows if row["performer_id"] is not None}
            if len(assigned) > 1:
                raise ValueError("Selected groups have different performer assignments; clear or align assignments first")
            db.conn.execute(f"UPDATE photo_face_detections SET group_id=?,updated_at=? WHERE group_id IN ({marks})", [target, now, *ids])
            performer_id = next(iter(assigned), None)
            db.conn.execute("UPDATE photo_face_groups SET performer_id=?,updated_at=? WHERE id=?", (performer_id, now, target))
            # Preserve source group records as empty groups: stable IDs ensure
            # UI references and logs remain intelligible, and merging can be
            # audited without deleting detection rows.
            for gid in ids:
                if gid != target:
                    db.conn.execute("UPDATE photo_face_groups SET label=?,performer_id=NULL,updated_at=? WHERE id=?",
                                    (f"Merged into group {target}", now, gid))
            db.conn.commit()
            return target
        except Exception:
            db.conn.rollback()
            raise


def _split_face_detection(db, detection_id: int) -> int:
    """Move one face detection to a fresh anonymous group without deleting it."""
    now = _now()
    with db.lock:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            row = db.conn.execute(
                "SELECT d.id,d.group_id,g.performer_id FROM photo_face_detections d "
                "JOIN photo_face_groups g ON g.id=d.group_id WHERE d.id=?", (int(detection_id),)
            ).fetchone()
            if not row:
                raise ValueError("Face detection not found")
            new_group = _insert_group(db.conn, now)
            db.conn.execute("UPDATE photo_face_detections SET group_id=?,updated_at=? WHERE id=?",
                            (new_group, now, int(detection_id)))
            db.conn.commit()
            return new_group
        except Exception:
            db.conn.rollback()
            raise


def _separate_photo_detections(db, photo_id: int, group_ids: list[int]) -> list[dict[str, int]]:
    """Move this photo's detections out of the selected group(s) without deleting the photo."""
    ids = list(dict.fromkeys(int(value) for value in group_ids if int(value) > 0))
    if not ids or len(ids) > 700:
        raise ValueError("Select a valid face group first")
    now = _now()
    marks = ",".join("?" for _ in ids)
    with db.lock:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            photo = db.conn.execute("SELECT id FROM photo_library_items WHERE id=?", (int(photo_id),)).fetchone()
            if not photo:
                raise ValueError("Photo was not found in the local library")
            detections = db.conn.execute(
                f"SELECT id,group_id,face_index FROM photo_face_detections WHERE photo_id=? AND group_id IN ({marks}) ORDER BY face_index",
                [int(photo_id), *ids],
            ).fetchall()
            if not detections:
                raise ValueError("No face detections from this group are attached to this photo")
            moved: list[dict[str, int]] = []
            for detection in detections:
                new_group = _insert_group(db.conn, now)
                db.conn.execute(
                    "UPDATE photo_face_detections SET group_id=?,updated_at=? WHERE id=?",
                    (new_group, now, int(detection["id"])),
                )
                moved.append({"detection_id": int(detection["id"]), "old_group_id": int(detection["group_id"]), "new_group_id": new_group})
            db.conn.commit()
            return moved
        except Exception:
            db.conn.rollback()
            raise




# ==============================================================================
# SECTION: Job entry point
# Face analysis job.
# ==============================================================================

async def run(ctx) -> dict[str, Any]:
    """Analyze unprocessed indexed photos; produce anonymous, editable face groups."""
    try:
        detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        detail = {}
    raw_channel_ids = detail.get("channel_ids") if isinstance(detail, dict) else []
    channel_ids = list(dict.fromkeys(_text(v) for v in (raw_channel_ids or []) if _text(v)))
    where = ["(a.photo_id IS NULL OR a.status<>'COMPLETE')"]
    params: list[Any] = []
    if channel_ids:
        marks = ",".join("?" for _ in channel_ids)
        where.append(f"p.channel_id IN ({marks})")
        params.extend(channel_ids)
    with ctx.db.reading() as rdb:
        rows = rdb.execute(
            f"""SELECT p.id,p.channel_id,p.message_id,p.filename,p.size,p.local_path
                FROM photo_library_items p LEFT JOIN photo_face_analysis a ON a.photo_id=p.id
                WHERE {' AND '.join(where)} ORDER BY p.message_date DESC,p.id DESC""", params
        ).fetchall()
    if not rows:
        with ctx.db.reading() as rdb:
            total = int(rdb.execute("SELECT COUNT(*) n FROM photo_library_items" + (f" WHERE channel_id IN ({','.join('?' for _ in channel_ids)})" if channel_ids else ""), channel_ids).fetchone()["n"] or 0)
        summary = "No indexed photos to analyze" if total == 0 else "All indexed photos in this selection have already been analyzed"
        from ..core.job_results import record as record_job_result
        record_job_result(ctx.db, ctx.job_id, status="COMPLETED", progress=100, summary=summary)
        ctx.report(completed=0, total=0, progress=100, current_item="", operation=summary, force=True)
        return {"photos_analyzed": 0, "faces_detected": 0, "summary": summary}

    client = getattr(ctx.telegram, "client", None)
    if client is None or not getattr(ctx.telegram, "connected", False):
        # Cached originals can still be used, but if none are available the normal
        # per-photo error below explains that Telegram must be connected.
        if not any(_stored_photo_path(row).is_file() for row in rows):
            raise RuntimeError("Connect Telegram or open these photos once so Archive X has local image copies before face analysis")

    ctx.report(completed=0, total=len(rows), current_item="", operation="Preparing local face-analysis models", force=True)
    yunet_path, sface_path = await ctx.blocking(_ensure_models)
    detector, recognizer = await ctx.blocking(_new_models, yunet_path, sface_path)
    exemplars = await ctx.blocking(_load_centroids, ctx.db)
    crop_dir = _cache_root() / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    analyzed = faces_total = failed = 0
    errors = []
    for index, row in enumerate(rows, start=1):
        await ctx.checkpoint()
        photo_id = int(row["id"])
        filename = _text(row["filename"]) or f"photo-{row['message_id']}"
        try:
            _set_analysis_state(ctx.db, photo_id, "SCANNING", "")
            image_path = await _ensure_photo_file(ctx, row)
            faces = await ctx.blocking(_extract_faces, str(image_path), str(crop_dir), detector, recognizer)
            await ctx.blocking(_store_photo_faces, ctx.db, photo_id, faces, exemplars)
            analyzed += 1
            faces_total += len(faces)
        except asyncio.CancelledError:
            _set_analysis_state(ctx.db, photo_id, "INTERRUPTED", "Analysis cancelled")
            raise
        except Exception as exc:
            failed += 1
            note = f"{filename}: {type(exc).__name__}: {exc}"
            errors.append(note[:500])
            _set_analysis_state(ctx.db, photo_id, "FAILED", note)
            ctx.db.log(f"Face analysis failed for {filename}: {type(exc).__name__}: {exc}", "WARN",
                       event="photo_library.face_analysis_failed", job_id=ctx.job_id,
                       details={"photo_id": photo_id, "channel_id": str(row["channel_id"]), "error": note[:500]})
        ctx.report(completed=index, total=len(rows), current_item=filename,
                   operation=f"Image-based face analysis · {index:,}/{len(rows):,} photos · {faces_total:,} faces",
                   force=(index == len(rows) or index % 5 == 0))
    with ctx.db.reading() as rdb:
        groups_count = int(rdb.execute("SELECT COUNT(*) n FROM photo_face_groups g WHERE EXISTS (SELECT 1 FROM photo_face_detections d WHERE d.group_id=g.id)").fetchone()["n"] or 0)
    summary = f"Analyzed {analyzed:,} photo(s); detected {faces_total:,} face(s) in {groups_count:,} visual groups"
    if failed:
        summary += f"; {failed:,} photo(s) failed and can be retried"
    from ..core.job_results import record as record_job_result
    record_job_result(ctx.db, ctx.job_id, status="COMPLETED_WITH_ERRORS" if failed else "COMPLETED",
                      progress=100, summary=summary)
    ctx.report(completed=len(rows), total=len(rows), progress=100, current_item="", operation=summary, force=True)
    ctx.db.log(summary, "SUCCESS" if not failed else "WARN", event="photo_library.face_analysis_completed",
               job_id=ctx.job_id, details={"photos_analyzed": analyzed, "faces_detected": faces_total,
                                           "visual_groups": groups_count, "errors": errors[:20]})
    return {"photos_analyzed": analyzed, "faces_detected": faces_total, "groups": groups_count,
            "failed": failed, "errors": errors[:20], "summary": summary}
