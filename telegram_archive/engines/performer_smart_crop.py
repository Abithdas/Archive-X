"""One-time smart framing for automatically acquired performer artwork.

This helper only creates a derived 4:5 crop. It never changes a user's uploaded,
URL-selected, or explicitly provider-selected image. Callers enforce that policy
using the existing performers.image_locked/image_source fields before persisting.

OpenCV's Haar detector is used when available. A deterministic saliency/centered
crop is used as a fallback so older deployments remain functional if OpenCV has
not yet been installed.
"""
from __future__ import annotations

import io
from typing import Callable

TARGET_ASPECT = 4 / 5
_MIN_DIMENSION = 96


def _find_face_box(image, cv2_module=None):
    """Return the largest detected face as (left, top, width, height), or None."""
    try:
        if cv2_module is None:
            import cv2 as cv2_module  # type: ignore[no-redef]
        import numpy as np

        from PIL import Image

        rgb = image.convert("RGB")
        scale = min(1.0, 900.0 / max(rgb.size))
        if scale < 1.0:
            sample = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))))
        else:
            sample = rgb
        gray = cv2_module.cvtColor(np.asarray(sample), cv2_module.COLOR_RGB2GRAY)
        gray = cv2_module.equalizeHist(gray)
        cascade_path = str(cv2_module.data.haarcascades) + "haarcascade_frontalface_default.xml"
        detector = cv2_module.CascadeClassifier(cascade_path)
        if detector.empty():
            return None
        min_side = max(24, min(gray.shape[:2]) // 18)
        boxes = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side))
        if len(boxes) == 0:
            return None
        inv = 1.0 / scale
        candidates = [
            (int(round(x * inv)), int(round(y * inv)), int(round(w * inv)), int(round(h * inv)))
            for x, y, w, h in boxes
        ]
        # Largest face is the best default for a single-person profile image.
        return max(candidates, key=lambda b: b[2] * b[3])
    except Exception:
        return None


def _clamp_origin(value: float, max_origin: int) -> int:
    return max(0, min(int(round(value)), max_origin))


def _fallback_saliency_box(image, crop_w: int, crop_h: int) -> tuple[int, int, int, int]:
    """Find a detail-rich crop using Pillow only when face detection is unavailable."""
    from PIL import ImageFilter, ImageStat

    w, h = image.size
    max_x, max_y = max(0, w - crop_w), max(0, h - crop_h)
    # The crop keeps the largest possible source area. Search coarse offsets only
    # along dimensions that need trimming, avoiding expensive full-resolution work.
    xs = sorted({int(round(max_x * i / 12)) for i in range(13)}) if max_x else [0]
    ys = sorted({int(round(max_y * i / 12)) for i in range(13)}) if max_y else [0]
    best = (max_x // 2, max_y // 2, float("-inf"))
    for top in ys:
        for left in xs:
            crop = image.crop((left, top, left + crop_w, top + crop_h)).convert("RGB")
            crop.thumbnail((72, 90))
            edges = crop.convert("L").filter(ImageFilter.FIND_EDGES)
            stat = ImageStat.Stat(edges)
            # Prefer texture/detail, with a light central bias to avoid cropping
            # logos or faces right against a boundary when scores are similar.
            center_penalty = abs((left + crop_w / 2) / max(1, w) - .5) + abs((top + crop_h / 2) / max(1, h) - .5)
            score = stat.mean[0] + stat.stddev[0] * .18 - center_penalty * 2.0
            if score > best[2]:
                best = (left, top, score)
    left, top, _score = best
    return left, top, left + crop_w, top + crop_h


def initial_performer_crop(data: bytes, *, face_detector: Callable | None = None) -> tuple[bytes, str, bool]:
    """Return (JPEG bytes or original bytes, MIME type, was_cropped).

    Targets 4:5 performer-card framing. If OpenCV finds a face, the crop is
    anchored around that face with headroom. Otherwise a detail-rich crop is used.
    Any decode/detection error returns the validated original image unchanged.
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.verify()
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")
    except Exception:
        return bytes(data), "image/jpeg", False

    w, h = image.size
    if min(w, h) < _MIN_DIMENSION:
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        return bytes(data), mime, False

    aspect = w / h
    if aspect > TARGET_ASPECT:
        crop_h = h
        crop_w = max(1, min(w, int(round(h * TARGET_ASPECT))))
    else:
        crop_w = w
        crop_h = max(1, min(h, int(round(w / TARGET_ASPECT))))

    face = face_detector(image) if face_detector else _find_face_box(image)
    # When the source already has the card aspect, only zoom/reframe a clearly
    # off-center face. Otherwise avoid making a needless, lossy derivative.
    if crop_w == w and crop_h == h:
        if not face:
            mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
            return bytes(data), mime, False
        fx, fy, fw, fh = face
        off_center = abs((fx + fw / 2) / w - .5) > .09 or abs((fy + fh / 2) / h - .38) > .12
        if not off_center:
            mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
            return bytes(data), mime, False
        crop_w = max(1, int(round(w * .90)))
        crop_h = max(1, int(round(crop_w / TARGET_ASPECT)))
        if crop_h > h:
            crop_h = max(1, int(round(h * .90)))
            crop_w = max(1, int(round(crop_h * TARGET_ASPECT)))

    if face:
        fx, fy, fw, fh = face
        # Set the face center about 38% down the portrait crop so that eyes and
        # forehead remain visible while the lower part of the portrait has room.
        left = _clamp_origin(fx + fw / 2 - crop_w / 2, max(0, w - crop_w))
        top = _clamp_origin(fy + fh / 2 - crop_h * .38, max(0, h - crop_h))
        box = (left, top, left + crop_w, top + crop_h)
    else:
        box = _fallback_saliency_box(image, crop_w, crop_h)

    if (box[2] - box[0]) < _MIN_DIMENSION or (box[3] - box[1]) < _MIN_DIMENSION:
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        return bytes(data), mime, False

    try:
        crop = image.crop(box)
        out = io.BytesIO()
        crop.save(out, format="JPEG", quality=91, optimize=True, progressive=True)
        result = out.getvalue()
        if not result:
            return bytes(data), "image/jpeg", False
        return result, "image/jpeg", True
    except Exception:
        return bytes(data), "image/jpeg", False
