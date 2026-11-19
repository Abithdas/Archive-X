"""Content tags for photos from a small local detector (YOLOv8 nano in ONNX, about 12 MB, an optional download).

For every photo it records which kinds of region are visible (for example breasts, buttocks, belly, feet) as plain tags that the
Photos page can filter on. It says what is visible, not size or shape, and never identifies anyone. Optional: the weights are
not part of the app (their licence is AGPL-3.0); they are downloaded when the person asks, under AI, Models, and run by
`content_detector` with onnxruntime. Without them the app runs as before and the feature says how to get them.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .. import config

MIN_SCORE = 0.5
# NudeNet class -> app tag. Covered regions are ignored (they say nothing about what is visible); faces belong to People.
CLASS_TO_TAG = {
    "FEMALE_BREAST_EXPOSED": "breasts",
    "BUTTOCKS_EXPOSED": "buttocks",
    "FEMALE_GENITALIA_EXPOSED": "genitals_female",
    "MALE_GENITALIA_EXPOSED": "genitals_male",
    "ANUS_EXPOSED": "anus",
    "MALE_BREAST_EXPOSED": "chest_male",
    "BELLY_EXPOSED": "belly",
    "ARMPITS_EXPOSED": "armpits",
    "FEET_EXPOSED": "feet",
}
TAG_LABELS = {
    "breasts": "Breasts", "buttocks": "Buttocks", "genitals_female": "Female genitals", "genitals_male": "Male genitals", "anus": "Anus",
    "chest_male": "Male chest", "belly": "Belly", "armpits": "Armpits", "feet": "Feet",
}


def available() -> bool:
    """The packages that run the detector are installed (the weights are a separate download: see model_ready)."""
    try:
        import importlib.util
        return all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "cv2", "numpy", "PIL"))
    except Exception:
        return False


def model_path():
    from ..ai import model_hub as hub
    return hub.active_dir("content") / "320n.onnx"


def model_ready() -> bool:
    from ..ai import model_hub as hub
    return hub.installed("content", hub.selected("content"))


def tags_from_detections(detections: list[dict[str, Any]], min_score: float = MIN_SCORE) -> dict[str, dict[str, float]]:
    """{tag: {"score": best score, "regions": how many}} from NudeNet's list of {class, score, box}."""
    out: dict[str, dict[str, float]] = {}
    for d in detections or []:
        tag = CLASS_TO_TAG.get(str(d.get("class") or ""))
        score = float(d.get("score") or 0)
        if not tag or score < min_score:
            continue
        cur = out.setdefault(tag, {"score": 0.0, "regions": 0})
        cur["score"] = max(cur["score"], score)
        cur["regions"] += 1
    return out


def store_tags(db, photo_id: int, tags: dict[str, dict[str, float]], status: str = "COMPLETE", error: str = "") -> None:
    now = config.now_iso()
    with db.lock:
        db.conn.execute("DELETE FROM photo_content_tags WHERE photo_id=?", (int(photo_id),))
        for tag, v in tags.items():
            db.conn.execute("INSERT INTO photo_content_tags(photo_id,tag,score,regions) VALUES(?,?,?,?)", (int(photo_id), tag, round(float(v["score"]), 4), int(v["regions"])))
        db.conn.execute("INSERT OR REPLACE INTO photo_content_analysis(photo_id,status,error,updated_at) VALUES(?,?,?,?)", (int(photo_id), status, error[:500], now))
        db.conn.commit()


def facet_counts(db, where: list[str], params: list[Any], ticked: list[str]) -> dict[str, int]:
    """How many photos carry each tag among the photos that pass the current filters (`where`/`params`, written for the aliases
    p = photo_library_items and c = channels) and already have every tag in `ticked`. A tag you could still add shows how many photos
    would be left if you ticked it; a tag you ticked shows how many are left now."""
    clause = list(where)
    args = list(params)
    for tag in ticked:
        clause.append("EXISTS (SELECT 1 FROM photo_content_tags k WHERE k.photo_id=p.id AND k.tag=?)")
        args.append(tag)
    with db.reading() as rdb:
        rows = rdb.execute(
            "SELECT ct.tag AS tag, COUNT(DISTINCT ct.photo_id) AS n FROM photo_content_tags ct "
            "JOIN photo_library_items p ON p.id=ct.photo_id LEFT JOIN channels c ON c.telegram_id=p.channel_id "
            f"WHERE {' AND '.join(clause)} GROUP BY ct.tag", args).fetchall()
    return {r["tag"]: int(r["n"]) for r in rows}


def summary(db, facets: dict[str, int] | None = None) -> dict[str, Any]:
    """Tags that exist, with a photo count each. With `facets` (see facet_counts) the counts are the ones inside the current filters;
    a tag that exists keeps its place even when no photo is left for it, so the chips do not jump about."""
    with db.reading() as rdb:
        total = int(rdb.execute("SELECT COUNT(*) n FROM photo_library_items").fetchone()["n"] or 0)
        done = int(rdb.execute("SELECT COUNT(*) n FROM photo_content_analysis WHERE status='COMPLETE'").fetchone()["n"] or 0)
        rows = rdb.execute("SELECT tag, COUNT(*) n FROM photo_content_tags GROUP BY tag").fetchall()
    counts = {r["tag"]: int(r["n"]) for r in rows}
    shown = counts if facets is None else facets
    return {"available": available() and model_ready(), "model_ready": model_ready(), "packages": available(), "total_photos": total, "analysed_photos": done,
            "tags": [{"tag": t, "label": TAG_LABELS[t], "count": shown.get(t, 0)} for t in TAG_LABELS if counts.get(t, 0)]}


async def run(ctx) -> dict[str, Any]:
    """Tag every photo that has not been analysed yet (or all of them with detail.redo)."""
    from .photo_face_clustering import _ensure_photo_file, _stored_photo_path
    try:
        detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        detail = {}
    if not available():
        raise RuntimeError("Content tags need onnxruntime and OpenCV, which this install does not have")
    if not model_ready():
        raise RuntimeError("Download the content model first: AI, Models, Content tags")
    redo = bool(detail.get("redo")) if isinstance(detail, dict) else False
    with ctx.db.reading() as rdb:
        rows = rdb.execute(
            f"""SELECT p.id,p.channel_id,p.message_id,p.filename,p.size,p.local_path FROM photo_library_items p
                LEFT JOIN photo_content_analysis a ON a.photo_id=p.id
                {'' if redo else "WHERE a.photo_id IS NULL OR a.status<>'COMPLETE'"} ORDER BY p.message_date DESC,p.id DESC""").fetchall()
    if not rows:
        ctx.report(completed=0, total=0, progress=100, current_item="", operation="All photos already have content tags", force=True)
        return {"photos_tagged": 0, "summary": "All photos already have content tags"}
    client = getattr(ctx.telegram, "client", None)
    if (client is None or not getattr(ctx.telegram, "connected", False)) and not any(_stored_photo_path(r).is_file() for r in rows):
        raise RuntimeError("Connect Telegram or open these photos once so Archive X has local image copies before tagging")
    ctx.report(completed=0, total=len(rows), current_item="", operation="Preparing the content detector", force=True)
    from .content_detector import ContentDetector
    detector = await ctx.blocking(ContentDetector, model_path())
    tagged = failed = 0
    for i, row in enumerate(rows, start=1):
        await ctx.checkpoint()
        pid = int(row["id"])
        name = str(row["filename"] or f"photo-{row['message_id']}")
        try:
            path = await _ensure_photo_file(ctx, row)
            dets = await ctx.blocking(detector.detect, str(path))
            await ctx.blocking(store_tags, ctx.db, pid, tags_from_detections(dets))
            tagged += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            await ctx.blocking(store_tags, ctx.db, pid, {}, "FAILED", f"{type(exc).__name__}: {exc}")
        ctx.report(completed=i, total=len(rows), current_item=name, operation=f"Tagging photos by content · {i:,}/{len(rows):,}", force=(i == len(rows) or i % 10 == 0))
    text = f"Tagged {tagged:,} photo(s)" + (f"; {failed:,} could not be read and can be retried" if failed else "")
    from ..core.job_results import record as record_job_result
    record_job_result(ctx.db, ctx.job_id, status="COMPLETED_WITH_ERRORS" if failed else "COMPLETED", progress=100, summary=text)
    ctx.report(completed=len(rows), total=len(rows), progress=100, current_item="", operation=text, force=True)
    return {"photos_tagged": tagged, "failed": failed, "summary": text}
