"""The sizes of every local model, and what can be done with each: download it, switch to it, delete it.

A model family (assistant, live captions, faces, content tags, look suggestions) can have several variants, listed from the
recommended one upwards (then the lighter ones). Nothing is bundled and nothing downloads by itself: a variant is fetched when the
person presses Download, kept when it matches its pinned SHA-256, and removed again when they press Delete. The choice of variant is
kept in one small file, so the engines (which run in jobs, outside any request) can read it.

Families whose files are plain downloads (faces, content tags, looks) are handled here. The assistant (llama.cpp, one GGUF per size)
and the live captions (a faster-whisper folder per size) keep their own storage; this module only tells the page about them.
"""
from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import config

HF = "https://huggingface.co/"
MIN_BYTES = 1024


@dataclass(frozen=True)
class File:
    name: str
    urls: tuple[str, ...]
    sha256: str
    size: int


@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    size_mb: int
    note: str
    recommended: bool = False
    files: tuple[File, ...] = ()


CLIP_B32 = "Xenova/clip-vit-base-patch32/resolve/main/"
CLIP_B16 = "Xenova/clip-vit-base-patch16/resolve/main/"

FAMILIES: dict[str, list[Variant]] = {
    "faces": [
        Variant("standard", "Standard", 39, "YuNet finds faces and SFace gives each a fingerprint. The only pair offered: a face model with a different fingerprint would not match the people already found, so it is not offered as a swap.", True, (
            File("face_detection_yunet_2023mar.onnx", ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", HF + "opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx"),
                 "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", 232_589),
            File("face_recognition_sface_2021dec.onnx", ("https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx", HF + "opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx"),
                 "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", 38_696_353))),
    ],
    "content": [
        Variant("fast", "Standard", 12, "A small detector (YOLOv8 nano, 320 pixels) that says which regions are visible in a photo. It is the only one offered: its licence (AGPL-3.0) is why it is a download you ask for and not part of the app.", True, (
            File("320n.onnx", (HF + "deepghs/nudenet_onnx/resolve/main/320n.onnx",), "c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f", 12_150_158),)),
    ],
    "looks": [
        Variant("b32", "Standard", 155, "CLIP ViT-B/32, the recommended one: quick, and good enough for hair colour and length.", True, (
            File("vision_model_quantized.onnx", (HF + CLIP_B32 + "onnx/vision_model_quantized.onnx",), "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299", 89_117_001),
            File("text_model_quantized.onnx", (HF + CLIP_B32 + "onnx/text_model_quantized.onnx",), "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a", 64_504_507),
            File("tokenizer.json", (HF + CLIP_B32 + "tokenizer.json",), "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd", 2_224_081))),
        Variant("b16", "Better", 154, "CLIP ViT-B/16 looks at the picture in finer pieces, so it tells shades and lengths apart a little better. A bit slower per photo.", False, (
            File("vision_model_quantized.onnx", (HF + CLIP_B16 + "onnx/vision_model_quantized.onnx",), "44eece4fe5fe4e0359a88268a327adf758633a1aade3917690b952bef1501f96", 87_461_602),
            File("text_model_quantized.onnx", (HF + CLIP_B16 + "onnx/text_model_quantized.onnx",), "9106b51e6c663a56b99182ec617c2b3d53577b037e7e24a7717eb78048a0c97a", 64_504_507),
            File("tokenizer.json", (HF + CLIP_B16 + "tokenizer.json",), "72ed5c96db5729294468543e4bc75fce14ca63f58e37300290189ba1c1e52b85", 2_224_081))),
    ],
}


def models_root() -> Path:
    return Path(config.CACHE_DIR) / "models"


def variants(family: str) -> list[Variant]:
    return FAMILIES[family]


def variant(family: str, variant_id: str) -> Variant:
    for v in FAMILIES.get(family, []):
        if v.id == variant_id:
            return v
    raise KeyError(f"{family}/{variant_id}")


def recommended(family: str) -> str:
    return next((v.id for v in FAMILIES[family] if v.recommended), FAMILIES[family][0].id)


def variant_dir(family: str, variant_id: str) -> Path:
    """Where a variant's files live. The recommended variants keep the folders earlier versions already used, so nothing is downloaded twice."""
    if family == "faces":
        from ..engines import photo_face_clustering as pf
        return pf._cache_root() / "models"
    if family == "looks":
        from ..engines import photo_look_tags as lt
        return lt._root() if variant_id == "b32" else lt._root() / variant_id
    return models_root() / family / variant_id


# ---- the chosen variant ----------------------------------------------------------------------------------------------------------------

_LOCK = threading.Lock()


def _choices_path() -> Path:
    return models_root() / "choices.json"


def _choices() -> dict[str, str]:
    try:
        data = json.loads(_choices_path().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def selected(family: str) -> str:
    """The variant the engines use: the one chosen, else the recommended one."""
    pick = _choices().get(family, "")
    return pick if any(v.id == pick for v in FAMILIES[family]) else recommended(family)


def select(family: str, variant_id: str) -> None:
    variant(family, variant_id)
    with _LOCK:
        data = _choices()
        data[family] = variant_id
        path = _choices_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


def active_dir(family: str) -> Path:
    return variant_dir(family, selected(family))


# ---- files -----------------------------------------------------------------------------------------------------------------------------

def installed(family: str, variant_id: str) -> bool:
    """Every file is there (a quick look at sizes; the checksum was checked when it was downloaded)."""
    v = variant(family, variant_id)
    root = variant_dir(family, variant_id)
    return all((root / f.name).is_file() and (root / f.name).stat().st_size >= min(MIN_BYTES, f.size) for f in v.files)


def disk_bytes(family: str, variant_id: str) -> int:
    root = variant_dir(family, variant_id)
    return sum((root / f.name).stat().st_size for f in variant(family, variant_id).files if (root / f.name).is_file())


def fetch(family: str, variant_id: str, on_progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> Path:
    """Download what is missing (blocking). Each file is kept only if it has its pinned SHA-256; a file already there that matches is left alone."""
    from . import local_ai_setup as las
    v = variant(family, variant_id)
    root = variant_dir(family, variant_id)
    root.mkdir(parents=True, exist_ok=True)
    cancel = cancel or threading.Event()
    total = sum(f.size for f in v.files)
    base = 0
    for f in v.files:
        dest = root / f.name
        try:
            ok = dest.is_file() and las._sha(dest, cancel=cancel) == f.sha256
        except OSError:
            ok = False
        if not ok:
            last: Exception | None = None
            for url in f.urls:
                try:
                    las.download(url, dest, sha256=f.sha256, size_hint=f.size, on_bytes=lambda done, _t, b=base: on_progress and on_progress(b + done, total), cancel=cancel)
                    last = None
                    break
                except las.Cancelled:
                    raise
                except Exception as exc:   # try the mirror
                    last = exc
            if last is not None:
                raise last
        base += f.size
        if on_progress:
            on_progress(base, total)
    return root


def remove(family: str, variant_id: str) -> int:
    """Delete a variant's files; returns the bytes freed. Partial downloads go too."""
    v = variant(family, variant_id)
    root = variant_dir(family, variant_id)
    freed = 0
    for f in v.files:
        for p in (root / f.name, root / (f.name + ".part")):
            try:
                if p.is_file():
                    freed += p.stat().st_size
                    p.unlink()
            except OSError:
                pass
    if family not in {"faces", "looks"} or variant_id not in {"standard", "b32"}:   # a folder this module made; the legacy ones may hold other things
        try:
            root.rmdir()
        except OSError:
            pass
    return freed


def dir_bytes(path: Path) -> int:
    """Size of everything under a folder (used for a download whose files are fetched by a library)."""
    total = 0
    try:
        for p in Path(path).rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def rmtree(path: Path) -> int:
    freed = dir_bytes(path)
    shutil.rmtree(path, ignore_errors=True)
    return freed
