"""Small dependency-light perceptual image hashes for duplicate refinement.

The archive already stores Telegram thumbnails locally. These hashes add a
second similarity signal after exact fingerprints, without downloading or
decoding the full video. They are deliberately deterministic and cheap:
64-bit average hash, dHash, and an 8x8 low-frequency DCT pHash.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import io
import math


def _gray(data: bytes, size: int):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.convert("L").resize((size, size), Image.Resampling.LANCZOS)


def _bits_to_hex(bits: list[int]) -> str:
    out = 0
    for bit in bits:
        out = (out << 1) | (1 if bit else 0)
    return f"{out:016x}"


def ahash64(data: bytes) -> str:
    im = _gray(data, 8)
    px = list(im.tobytes())
    avg = sum(px) / len(px)
    return _bits_to_hex([1 if v >= avg else 0 for v in px])


def dhash64(data: bytes) -> str:
    im = _gray(data, 9)
    px = list(im.tobytes())
    bits = []
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits.append(1 if px[row + x] > px[row + x + 1] else 0)
    return _bits_to_hex(bits)


def phash64(data: bytes) -> str:
    """Return a standard 64-bit low-frequency DCT perceptual hash."""
    im = _gray(data, 32)
    px = [float(v) for v in im.tobytes()]
    n = 32
    coeffs = []
    # The low-frequency 8x8 DCT block is enough for near-duplicate artwork.
    for u in range(8):
        cu = 1.0 / math.sqrt(2.0) if u == 0 else 1.0
        for v in range(8):
            cv = 1.0 / math.sqrt(2.0) if v == 0 else 1.0
            total = 0.0
            for y in range(n):
                base = y * n
                cy = math.cos((2 * y + 1) * v * math.pi / (2 * n))
                for x in range(n):
                    total += px[base + x] * math.cos((2 * x + 1) * u * math.pi / (2 * n)) * cy
            coeffs.append(cu * cv * total / 4.0)
    median = sorted(coeffs[1:])[len(coeffs[1:]) // 2]
    return _bits_to_hex([1 if c >= median else 0 for c in coeffs])


def hamming_hex(a: str, b: str) -> int:
    try:
        return (int(str(a), 16) ^ int(str(b), 16)).bit_count()
    except Exception:
        return 64


def similarity(a: str, b: str) -> float:
    return max(0.0, 1.0 - hamming_hex(a, b) / 64.0)


def image_hashes(data: bytes) -> dict[str, str]:
    return {"ahash": ahash64(data), "dhash": dhash64(data), "phash": phash64(data)}


def stash_perception_hash(data: bytes) -> str:
    """Compute Stash/goimagehash-compatible 64-bit PerceptionHash for an image.

    Mirrors goimagehash.PerceptionHash: bilinear 64x64 resize, fast RGB luminance,
    unscaled DCT-II coefficients for the low-frequency 8x8 block, the median of
    those 64 coefficients, then one bit per coefficient. Hashes use Stash's
    lowercase, unpadded hexadecimal representation.
    """
    from PIL import Image
    import numpy as np
    import cv2
    with Image.open(io.BytesIO(data)) as source:
        resized = source.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
        rgb = np.asarray(resized, dtype=np.float32)
    # goimagehash's Rgb2GrayFast uses the same Rec.601 luminance coefficients.
    gray = (rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114).astype(np.float32)
    # OpenCV's DCT uses orthonormal coefficients, while goimagehash's
    # DCT2DFast64 uses the unscaled DCT-II transform. Undo OpenCV's per-axis
    # normalization before taking the median, otherwise values near the median
    # can flip bits and the result is not Stash-compatible.
    dct = cv2.dct(gray).astype(np.float64)[:8, :8]
    scale = np.array([8.0 if i == 0 else math.sqrt(32.0) for i in range(8)], dtype=np.float64)
    coeff = (dct * scale[:, None] * scale[None, :]).reshape(-1)
    median = float(np.median(coeff))
    value = 0
    for index, item in enumerate(coeff):
        value = (value << 1) | (1 if float(item) > median else 0)
    return format(value, "x")


def stash_video_phash_from_snapshots(frames_dir, duration: float) -> str | None:
    """Build Stash's 5x5 sprite from the closest existing 36 cached frames.

    Stash samples at 5% + i*(90%/25) of the duration, scales each screenshot to
    160 px wide with aspect preserved, pastes a row-major 5x5 montage, and applies
    goimagehash.PerceptionHash. Archive X reuses its scan-validated 36 snapshots
    (nearest timestamp to each required Stash sample) so matching never downloads
    or decodes the source video on its own.
    """
    from pathlib import Path
    from PIL import Image
    root = Path(frames_dir)
    duration = max(0.0, float(duration or 0))
    frame_paths = [root / f"frame_{idx:02d}.jpg" for idx in range(1, 37)]
    if not all(path.is_file() for path in frame_paths):
        return None
    from ..engines.preview_prefetch import frame_times
    times = frame_times(duration) if duration > 0 else [float(i) for i in range(36)]
    targets = [duration * (0.05 + i * (0.9 / 25.0)) for i in range(25)] if duration > 0 else [times[round(i*35/24)] for i in range(25)]
    with Image.open(frame_paths[0]) as first:
        w0, h0 = first.size
    if not w0 or not h0:
        return None
    cell_h = max(2, int(round(160 * h0 / w0)))
    if cell_h % 2: cell_h += 1
    sheet = Image.new("RGB", (800, cell_h * 5), (0, 0, 0))
    for idx, target in enumerate(targets):
        source_index = min(range(len(times)), key=lambda j: abs(float(times[j]) - target))
        with Image.open(frame_paths[source_index]) as frame:
            cell = frame.convert("RGB").resize((160, cell_h), Image.Resampling.BILINEAR)
            sheet.paste(cell, ((idx % 5) * 160, (idx // 5) * cell_h))
    output = io.BytesIO()
    sheet.save(output, format="PNG", optimize=False)
    return stash_perception_hash(output.getvalue())


def compare_scene_image_to_snapshots(image_data: bytes, frames_dir) -> dict:
    """Compare a provider scene still against all local 36 snapshots.

    Returns the strongest hash similarity and its frame index. This function is
    only called by the explicit Metadata & Assets scan.
    """
    from pathlib import Path
    from PIL import Image
    root = Path(frames_dir)
    try:
        reference = image_hashes(image_data)
    except Exception:
        return {"similarity": 0.0, "phash_distance": 64, "dhash_distance": 64, "frame_index": None}
    best = {"similarity": 0.0, "phash_distance": 64, "dhash_distance": 64, "frame_index": None}
    for idx in range(1, 37):
        path = root / f"frame_{idx:02d}.jpg"
        try:
            data = path.read_bytes()
            hashes = image_hashes(data)
            pd = hamming_hex(reference["phash"], hashes["phash"])
            dd = hamming_hex(reference["dhash"], hashes["dhash"])
            similarity_score = max(0.0, 1.0 - (0.7 * pd + 0.3 * dd) / 64.0)
            if similarity_score > float(best["similarity"]):
                best = {"similarity": round(similarity_score, 4), "phash_distance": pd,
                        "dhash_distance": dd, "frame_index": idx - 1}
        except Exception:
            _swallowed_note("perceptual.compare_scene_image_to_snapshots")
            continue
    return best
