"""Runs the content-region detector (a YOLOv8 nano model in ONNX form) with onnxruntime and numpy.

Nothing of the NudeNet package is used or shipped: the weights are an optional download (AI, Models), and this file only knows the
public YOLOv8 conventions: the picture is padded to a square at the bottom and right, scaled to the model size, divided by 255 and fed
as RGB; every candidate has four box numbers and one score per class; overlapping boxes are thinned by non-maximum
suppression. The class names are read from the model file itself (Ultralytics stores them there), with the 18 NudeNet names as the
fallback for a file that has none.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

FALLBACK_LABELS = [
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED",
    "ANUS_EXPOSED", "FEET_EXPOSED", "BELLY_COVERED", "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED",
    "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED",
]
CANDIDATE_SCORE = 0.20   # a candidate below this is dropped before thinning
NMS_SCORE = 0.25         # and one below this does not survive thinning
NMS_IOU = 0.45           # two boxes of one class that overlap more than this are the same thing


def labels_from_metadata(meta: dict[str, str] | None) -> list[str]:
    """Class names from the model's own metadata ("{0: 'NAME', ...}"), or the built-in 18."""
    try:
        names = ast.literal_eval((meta or {}).get("names", ""))
        if isinstance(names, dict) and names:
            return [str(names[i]) for i in sorted(names)]
    except Exception:
        pass
    return list(FALLBACK_LABELS)


def letterbox(image, size: int):
    """PIL image -> (tensor 1x3xSxS in 0..1, padded side). The picture sits top-left on a black square, then the square is resized."""
    import numpy as np
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(image).convert("RGB")
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(im, (0, 0))
    import cv2   # the same plain bilinear resize the model was trained and exported with
    arr = cv2.resize(np.asarray(canvas), (size, size), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    return arr.transpose(2, 0, 1)[None].copy(), (w, h), side


def non_max_suppression(boxes, scores, iou: float = NMS_IOU, min_score: float = NMS_SCORE) -> list[int]:
    """Indexes to keep: highest score first, a box is dropped when a kept box overlaps it by more than `iou` (whatever the class: one
    place on the body gets one label, the best one)."""
    import numpy as np
    order = [i for i in np.argsort(-np.asarray(scores, dtype=np.float64)) if scores[i] >= min_score]
    keep: list[int] = []
    for i in order:
        x1, y1, w1, h1 = boxes[i]
        ok = True
        for j in keep:
            x2, y2, w2, h2 = boxes[j]
            iw = min(x1 + w1, x2 + w2) - max(x1, x2)
            ih = min(y1 + h1, y2 + h2) - max(y1, y2)
            if iw <= 0 or ih <= 0:
                continue
            inter = iw * ih
            union = w1 * h1 + w2 * h2 - inter
            if union > 0 and inter / union > iou:
                ok = False
                break
        if ok:
            keep.append(int(i))
    return keep


def decode(output, labels: list[str], size: int, original: tuple[int, int], side: int) -> list[dict[str, Any]]:
    """Model output (1, 4+classes, candidates) -> [{"class", "score", "box": [x, y, w, h]}] in the original picture's pixels."""
    import numpy as np
    arr = np.asarray(output)
    rows = (arr[0] if arr.ndim == 3 else arr).T    # (candidates, 4 + classes)
    if rows.ndim != 2 or rows.shape[1] < 5:
        return []
    ow, oh = original
    scale = side / float(size)
    cls = rows[:, 4:]
    best = cls.max(axis=1)
    ids = cls.argmax(axis=1)
    boxes: list[list[float]] = []
    scores: list[float] = []
    kinds: list[int] = []
    for k in np.nonzero(best >= CANDIDATE_SCORE)[0]:
        cx, cy, w, h = (float(v) for v in rows[k, :4])
        x, y = (cx - w / 2) * scale, (cy - h / 2) * scale
        w, h = w * scale, h * scale
        x, y = max(0.0, min(x, ow)), max(0.0, min(y, oh))
        w, h = min(w, ow - x), min(h, oh - y)
        if w < 1 or h < 1:   # nothing of it is inside the picture
            continue
        boxes.append([x, y, w, h]); scores.append(float(best[k])); kinds.append(int(ids[k]))
    out = []
    for i in non_max_suppression(boxes, scores):
        c = kinds[i]
        x, y, w, h = boxes[i]
        out.append({"class": labels[c] if c < len(labels) else f"CLASS_{c}", "score": scores[i], "box": [int(x), int(y), int(w), int(h)]})
    return out


class ContentDetector:
    """The loaded detector. `detect(path)` returns the same list NudeNet's own detector returned, so the tag mapping did not change."""

    def __init__(self, model_path: str | Path):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.size = int(shape[2]) if len(shape) == 4 and isinstance(shape[2], int) else 320
        self.labels = labels_from_metadata(self.session.get_modelmeta().custom_metadata_map)

    def detect_image(self, image) -> list[dict[str, Any]]:
        tensor, original, side = letterbox(image, self.size)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode(output, self.labels, self.size, original, side)

    def detect(self, path: str) -> list[dict[str, Any]]:
        from PIL import Image
        with Image.open(path) as im:
            return self.detect_image(im)
