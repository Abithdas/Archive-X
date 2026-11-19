"""Suggested looks for photos (hair colour, hair length) from a small local CLIP model.

CLIP ViT-B/32 (OpenAI, MIT licence), quantised ONNX: about 155 MB, downloaded once and checked against a fixed SHA-256.
Each photo is compared with short text prompts ("a photo of a person with red hair"); inside a group the prompt that fits best
wins if it is clearly ahead. Only photos that look like they show a person are tagged. These are SUGGESTIONS: they can be wrong,
and the score is kept so the Photos page can say so. Nothing here identifies anyone.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

from .. import config

_BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
FILES = {
    "vision_model_quantized.onnx": ("onnx/vision_model_quantized.onnx", "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
    "text_model_quantized.onnx": ("onnx/text_model_quantized.onnx", "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"),
    "tokenizer.json": ("tokenizer.json", "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd"),
}
MEAN = (0.48145466, 0.4578275, 0.40821073)
STD = (0.26862954, 0.26130258, 0.27577711)
MIN_PROB = 0.40      # the winning look must have at least this share of its group
MIN_MARGIN = 0.20    # and lead the runner-up by this much
PERSON_MIN = 0.60    # share of "a photo of a person" among the person/not-person prompts

PERSON_PROMPTS = ["a photo of a person", "a portrait of a woman", "a portrait of a man",
                  "a photo of a landscape", "a photo of an animal", "a photo of an object", "a photo of food", "a screenshot of text"]
N_PERSON_YES = 3     # the first three prompts above mean "a person"
# Each look is a short phrase that is slotted into several templates and averaged (a standard way to make CLIP steadier).
TEMPLATES = ["a photo of a person with {}", "a close-up portrait of someone with {}", "a woman or man with {}"]
# group -> (tag, label, phrase); inside a group the phrases compete with each other
LOOKS: dict[str, list[tuple[str, str, str]]] = {
    "Hair colour": [
        ("hair_blonde", "Blonde", "blonde hair"),
        ("hair_brunette", "Brunette", "dark brown hair"),
        ("hair_black", "Black hair", "black hair"),
        ("hair_red", "Redhead", "red ginger hair"),
        ("hair_gray", "Grey or white", "gray or white hair"),
        ("hair_dyed", "Dyed (pink, blue, purple)", "pink, blue or purple dyed hair"),
    ],
    "Hair length": [
        ("hair_long", "Long hair", "long hair past the shoulders"),
        ("hair_medium", "Shoulder length", "shoulder length hair"),
        ("hair_short", "Short hair", "short hair"),
        ("hair_bob", "Bob cut", "a bob haircut"),
        ("hair_bald", "Shaved or bald", "a shaved head"),
    ],
}
LOOK_LABELS = {tag: label for group in LOOKS.values() for tag, label, _ in group}
LOOK_GROUP = {tag: name for name, group in LOOKS.items() for tag, _, _ in group}


def available() -> bool:
    try:
        import importlib.util
        return all(importlib.util.find_spec(m) for m in ("onnxruntime", "tokenizers", "numpy", "PIL"))
    except Exception:
        return False


def _root() -> Path:
    return Path(config.CACHE_DIR) / "look_models"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_models(root: Path | None = None) -> Path:
    """Download the three model files once; each is verified before it is kept. Without `root` this is the size chosen under AI, Models."""
    if root is None:
        from ..core import model_hub as hub
        return hub.fetch("looks", hub.selected("looks"))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, (remote, digest) in FILES.items():
        dest = root / name
        if dest.is_file() and dest.stat().st_size > 1024 and _sha(dest) == digest:
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(_BASE + remote, headers={"User-Agent": "ArchiveX-local-look-tags/1.0"})
            h = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=60) as r, part.open("wb") as out:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    h.update(chunk); out.write(chunk)
            if h.hexdigest() != digest:
                raise RuntimeError(f"Checksum mismatch for {name}; it was not installed")
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)
    return root


def preprocess(image):
    """PIL image -> CLIP input (1, 3, 224, 224): shortest side to 224 (bicubic), centre crop, normalise."""
    import numpy as np
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(image).convert("RGB")
    w, h = im.size
    scale = 224 / min(w, h)
    im = im.resize((max(224, round(w * scale)), max(224, round(h * scale))), Image.Resampling.BICUBIC)
    w, h = im.size
    left, top = (w - 224) // 2, (h - 224) // 2
    arr = np.asarray(im.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255.0
    arr = (arr - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    return arr.transpose(2, 0, 1)[None].astype(np.float32)


class LookModel:
    """The loaded CLIP model with every prompt already turned into a vector (the text side runs once)."""

    def __init__(self, root: Path | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        root = Path(root or _root())
        opts = ort.SessionOptions(); opts.intra_op_num_threads = 2
        self.vision = ort.InferenceSession(str(root / "vision_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
        text = ort.InferenceSession(str(root / "text_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(root / "tokenizer.json"))
        tok.enable_truncation(max_length=77)
        import numpy as np
        phrases = [p for g in LOOKS.values() for _, _, p in g]
        prompts = PERSON_PROMPTS + [t.format(ph) for ph in phrases for t in TEMPLATES]
        ids = [e.ids for e in tok.encode_batch(prompts)]
        width = max(len(i) for i in ids)
        batch = np.asarray([i + [0] * (width - len(i)) for i in ids], dtype=np.int64)   # CLIP pools at the highest id (end token), so zero padding is safe
        emb = text.run(None, {"input_ids": batch})[0].astype(np.float32)
        emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        n = len(PERSON_PROMPTS)
        look = emb[n:].reshape(len(phrases), len(TEMPLATES), -1).mean(axis=1)
        self.person_text = emb[:n]
        self.look_text = look / np.linalg.norm(look, axis=1, keepdims=True)

    def embed(self, image):
        import numpy as np
        v = self.vision.run(None, {"pixel_values": preprocess(image)})[0].astype(np.float32)[0]
        return v / np.linalg.norm(v)

    def tags(self, image, face_box=None) -> dict[str, dict[str, float]]:
        """Suggested tags for one photo. With a detected face (x, y, w, h) the head and hair are cropped out first, which matters a lot."""
        if face_box is not None:
            image = head_crop(image, face_box)
        return choose_tags(self.scores(self.embed(image)), has_face=face_box is not None)

    def scores(self, img_vec) -> dict[str, float]:
        """Share of each look among the looks it competes with (CLIP's usual temperature of 100)."""
        import numpy as np
        def soft(x):
            e = np.exp(x - x.max()); return e / e.sum()
        out = {"person": float(soft(100.0 * (self.person_text @ img_vec))[:N_PERSON_YES].sum())}
        logits = 100.0 * (self.look_text @ img_vec)
        i = 0
        for group in LOOKS.values():
            p = soft(logits[i: i + len(group)])
            for (tag, _label, _phrase), v in zip(group, p):
                out[tag] = float(v)
            i += len(group)
        return out


def head_crop(image, box):
    """Crop around a face box with room for hair above and at the sides, and a little below."""
    x, y, w, h = [float(v) for v in box]
    left, top = max(0, int(x - 1.0 * w)), max(0, int(y - 0.9 * h))
    right, bottom = min(image.width, int(x + 2.0 * w)), min(image.height, int(y + 1.5 * h))
    return image.crop((left, top, max(left + 8, right), max(top + 8, bottom)))


def choose_tags(scores: dict[str, float], has_face: bool = False) -> dict[str, dict[str, float]]:
    """{tag: {"score": share}} for each group whose winner is clear. A photo with a detected face counts as a person; otherwise CLIP must say so."""
    if not has_face and scores.get("person", 0.0) < PERSON_MIN:
        return {}
    out: dict[str, dict[str, float]] = {}
    for group in LOOKS.values():
        ranked = sorted(((scores.get(tag, 0.0), tag) for tag, _, _ in group), reverse=True)
        (best, tag), second = ranked[0], ranked[1][0]
        if best >= MIN_PROB and best - second >= MIN_MARGIN:
            out[tag] = {"score": best, "regions": 1}
    return out


def largest_face(image, detector):
    """(x, y, w, h) of the biggest face YuNet finds in a PIL image, or None."""
    if detector is None:
        return None
    import cv2
    import numpy as np
    bgr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    detector.setInputSize((bgr.shape[1], bgr.shape[0]))
    _ok, found = detector.detect(bgr)
    if found is None or not len(found):
        return None
    best = max(found, key=lambda r: float(r[2]) * float(r[3]))
    return tuple(float(v) for v in best[:4])


def store(db, photo_id: int, tags: dict[str, dict[str, float]], status: str = "COMPLETE", error: str = "") -> None:
    now = config.now_iso()
    marks = ",".join("?" for _ in LOOK_LABELS)
    with db.lock:
        db.conn.execute(f"DELETE FROM photo_content_tags WHERE photo_id=? AND tag IN ({marks})", (int(photo_id), *LOOK_LABELS))
        for tag, v in tags.items():
            db.conn.execute("INSERT INTO photo_content_tags(photo_id,tag,score,regions) VALUES(?,?,?,1)", (int(photo_id), tag, round(float(v["score"]), 4)))
        db.conn.execute("INSERT OR REPLACE INTO photo_look_analysis(photo_id,status,error,updated_at) VALUES(?,?,?,?)", (int(photo_id), status, error[:500], now))
        db.conn.commit()


def summary(db, facets: dict[str, int] | None = None) -> dict[str, Any]:
    """Look tags that exist, with a photo count each; `facets` gives the counts inside the current filters (see photo_content_tags.facet_counts)."""
    with db.reading() as rdb:
        total = int(rdb.execute("SELECT COUNT(*) n FROM photo_library_items").fetchone()["n"] or 0)
        done = int(rdb.execute("SELECT COUNT(*) n FROM photo_look_analysis WHERE status='COMPLETE'").fetchone()["n"] or 0)
        counts = {r["tag"]: int(r["n"]) for r in rdb.execute("SELECT tag, COUNT(*) n FROM photo_content_tags WHERE tag LIKE 'hair\\_%' ESCAPE '\\' GROUP BY tag").fetchall()}
    shown = counts if facets is None else facets
    return {"available": available(), "total_photos": total, "analysed_photos": done,
            "tags": [{"tag": t, "label": LOOK_LABELS[t], "group": LOOK_GROUP[t], "count": shown.get(t, 0)} for t in LOOK_LABELS if counts.get(t)]}


async def run(ctx) -> dict[str, Any]:
    from .photo_face_clustering import _ensure_photo_file, _stored_photo_path
    from PIL import Image, ImageOps
    try:
        detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        detail = {}
    if not available():
        raise RuntimeError("Look suggestions need the optional packages: pip install onnxruntime tokenizers")
    redo = bool(detail.get("redo")) if isinstance(detail, dict) else False
    with ctx.db.reading() as rdb:
        rows = rdb.execute(
            f"""SELECT p.id,p.channel_id,p.message_id,p.filename,p.size,p.local_path FROM photo_library_items p
                LEFT JOIN photo_look_analysis a ON a.photo_id=p.id
                {'' if redo else "WHERE a.photo_id IS NULL OR a.status<>'COMPLETE'"} ORDER BY p.message_date DESC,p.id DESC""").fetchall()
    if not rows:
        ctx.report(completed=0, total=0, progress=100, current_item="", operation="All photos already have look suggestions", force=True)
        return {"photos_tagged": 0, "summary": "All photos already have look suggestions"}
    client = getattr(ctx.telegram, "client", None)
    if (client is None or not getattr(ctx.telegram, "connected", False)) and not any(_stored_photo_path(r).is_file() for r in rows):
        raise RuntimeError("Connect Telegram or open these photos once so Archive X has local image copies first")
    ctx.report(completed=0, total=len(rows), current_item="", operation="Getting the look model ready (it downloads the first time)", force=True)
    root = await ctx.blocking(ensure_models)
    model = await ctx.blocking(LookModel, root)
    detector = None
    try:   # the same small face detector as People, to find the head
        from .photo_face_clustering import _ensure_models, _new_models
        yunet, sface = await ctx.blocking(_ensure_models)
        detector, _ = await ctx.blocking(_new_models, yunet, sface)
    except Exception as exc:
        ctx.db.log(f"Look suggestions: face detector unavailable, using whole photos: {type(exc).__name__}: {exc}", "WARN")
    tagged = failed = 0
    for i, row in enumerate(rows, start=1):
        await ctx.checkpoint()
        pid = int(row["id"]); name = str(row["filename"] or f"photo-{row['message_id']}")
        try:
            path = await _ensure_photo_file(ctx, row)
            def work():
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    return model.tags(im, largest_face(im, detector))
            await ctx.blocking(store, ctx.db, pid, await ctx.blocking(work))
            tagged += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            await ctx.blocking(store, ctx.db, pid, {}, "FAILED", f"{type(exc).__name__}: {exc}")
        ctx.report(completed=i, total=len(rows), current_item=name, operation=f"Suggesting looks · {i:,}/{len(rows):,}", force=(i == len(rows) or i % 10 == 0))
    text = f"Looked at {tagged:,} photo(s)" + (f"; {failed:,} could not be read" if failed else "")
    from ..core.job_results import record as record_job_result
    record_job_result(ctx.db, ctx.job_id, status="COMPLETED_WITH_ERRORS" if failed else "COMPLETED", progress=100, summary=text)
    ctx.report(completed=len(rows), total=len(rows), progress=100, current_item="", operation=text, force=True)
    return {"photos_tagged": tagged, "failed": failed, "summary": text}
