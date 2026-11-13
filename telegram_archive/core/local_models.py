"""Every local AI model Archive X can use, in one list: what it is, what it powers, whether it is ready, a quick self-test, and download.

No model weights are bundled. Faces, look suggestions and live captions download once on request (checked where a checksum is pinned);
content tags ship inside the optional `nudenet` package; the assistant model is set up by hand on the Models tab.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import config

WHISPER_MB = {"tiny": 75, "base": 142, "small": 466, "medium": 1500, "large-v3": 2900}


def whisper_name() -> str:
    return os.environ.get("ARCHIVE_X_WHISPER_MODEL", "small")


def _whisper_dir() -> Path:
    return Path(config.PLAYER_CACHE_DIR) / "models"


def _whisper_cached() -> bool:
    root = _whisper_dir()
    return root.is_dir() and any(f"faster-whisper-{whisper_name()}" in str(p) for p in root.rglob("model.bin"))


def _has(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _faces_ready() -> bool:
    try:
        import hashlib
        from ..engines import photo_face_clustering as pf
        root = pf._cache_root() / "models"
        for name, digest in (("face_detection_yunet_2023mar.onnx", pf._YUNET_SHA256), ("face_recognition_sface_2021dec.onnx", pf._SFACE_SHA256)):
            f = root / name
            if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != digest:
                return False
        return True
    except Exception:
        return False


def _looks_ready() -> bool:
    from ..engines import photo_look_tags as lt
    root = lt._root()
    return lt.available() and all((root / n).is_file() and (root / n).stat().st_size > 1024 for n in lt.FILES)


def catalog(ai_status: dict | None = None) -> list[dict[str, Any]]:
    """The five local models with their current state (no downloads, no model loading)."""
    rt = (ai_status or {}).get("runtime") or {}
    llm_files = bool((rt.get("runtime") or {}).get("exists")) and bool((rt.get("model") or {}).get("exists"))
    llm_state = "ready" if rt.get("ready") else "stopped" if llm_files else "not_set_up"
    from ..engines import photo_content_tags as ct
    wname = whisper_name()
    cv_ok = _has("cv2")
    faces = _faces_ready()
    looks = _looks_ready()
    return [
        {"id": "llm", "name": "Assistant model (llama.cpp)", "detail": "Qwen3 4B Instruct, Q4_K_M, run by llama-server", "size_mb": 2500, "licence": "Apache-2.0 model, MIT runtime",
         "state": llm_state, "installable": False, "powers": ["AI Assistance: chat", "Library intelligence", "Diagnostics", "Jobs", "Recommendations"],
         "hint": "Set the llama-server path and the .gguf file on this page, then press Start." if llm_state == "not_set_up" else "Press Start local model." if llm_state == "stopped" else ""},
        {"id": "faces", "name": "Faces (YuNet + SFace)", "detail": "Finds faces and gives each a fingerprint so the same person can be grouped", "size_mb": 39, "licence": "Apache-2.0 (OpenCV Zoo)",
         "state": "ready" if faces else "needs_package" if not cv_ok else "not_downloaded", "installable": cv_ok and not faces,
         "powers": ["People and Find people", "Check faces", "Matching with performer pictures", "Head finding for look suggestions"],
         "hint": "" if faces else "Needs opencv-python-headless (pip install opencv-python-headless)." if not cv_ok else "Downloads about 39 MB once."},
        {"id": "captions", "name": f"Live captions (Whisper {wname})", "detail": "Speech to text in any language, with translation to English", "size_mb": WHISPER_MB.get(wname, 466), "licence": "MIT (faster-whisper)",
         "state": "needs_package" if not _has("faster_whisper") else "ready" if _whisper_cached() else "not_downloaded", "installable": _has("faster_whisper") and not _whisper_cached(),
         "powers": ["Video player: live captions (English or original language)", "Chinese, Japanese and other languages"],
         "hint": "Needs faster-whisper (pip install faster-whisper)." if not _has("faster_whisper") else "" if _whisper_cached() else f"Downloads about {WHISPER_MB.get(wname, 466)} MB once. Set ARCHIVE_X_WHISPER_MODEL for another size."},
        {"id": "content", "name": "Content tags (NudeNet)", "detail": "Finds which body-part regions are visible in a photo", "size_mb": 12, "licence": "MIT",
         "state": "ready" if ct.available() else "needs_package", "installable": False, "powers": ["Photos: Content tags and filters"],
         "hint": "" if ct.available() else "Needs the nudenet package (pip install nudenet); the 12 MB model is inside it."},
        {"id": "looks", "name": "Look suggestions (CLIP ViT-B/32)", "detail": "Suggests hair colour and hair length from the head in a photo", "size_mb": 155, "licence": "MIT (OpenAI CLIP)",
         "state": "needs_package" if not _has("tokenizers") or not _has("onnxruntime") else "ready" if looks else "not_downloaded", "installable": _has("tokenizers") and _has("onnxruntime") and not looks,
         "powers": ["Photos: Looks (suggested)"],
         "hint": "Needs onnxruntime and tokenizers (pip install onnxruntime tokenizers)." if not _has("tokenizers") or not _has("onnxruntime") else "" if looks else "Downloads about 155 MB once (checked against a fixed checksum)."},
    ]


def _blank_image(path: Path) -> None:
    from PIL import Image
    import numpy as np
    x = np.linspace(0, 255, 320, dtype=np.uint8)
    Image.fromarray(np.tile(x, (240, 1))[..., None].repeat(3, axis=2)).save(path)


def self_test(model_id: str) -> dict[str, Any]:
    """Load the model and run it once on a generated picture (or one second of silence). Never downloads."""
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "blank.png"
        if model_id == "faces":
            if not _faces_ready():
                return {"ok": False, "detail": "The face models are not downloaded yet."}
            from ..engines import photo_face_clustering as pf
            root = pf._cache_root() / "models"
            det, rec = pf._new_models(root / "face_detection_yunet_2023mar.onnx", root / "face_recognition_sface_2021dec.onnx")
            _blank_image(img)
            faces = pf._extract_faces(str(img), str(Path(tmp) / "crops"), det, rec)
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"Loaded and ran. {len(faces)} faces in the blank test picture (none expected)."}
        if model_id == "captions":
            if not _has("faster_whisper"):
                return {"ok": False, "detail": "faster-whisper is not installed."}
            if not _whisper_cached():
                return {"ok": False, "detail": "The speech model is not downloaded yet."}
            import numpy as np
            from faster_whisper import WhisperModel
            m = WhisperModel(whisper_name(), device="auto", compute_type="int8", download_root=str(_whisper_dir()), local_files_only=True)
            segs, info = m.transcribe(np.zeros(16000, dtype="float32"), beam_size=1)
            list(segs)
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": "Loaded and transcribed one second of silence."}
        if model_id == "content":
            from ..engines import photo_content_tags as ct
            if not ct.available():
                return {"ok": False, "detail": "nudenet is not installed."}
            from nudenet import NudeDetector
            _blank_image(img)
            dets = NudeDetector().detect(str(img))
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"Loaded and ran. {len(dets)} regions in the blank test picture (none expected)."}
        if model_id == "looks":
            from ..engines import photo_look_tags as lt
            if not _looks_ready():
                return {"ok": False, "detail": "The look model is not downloaded yet."}
            from PIL import Image
            _blank_image(img)
            m = lt.LookModel(lt._root())
            with Image.open(img) as im:
                scores = m.scores(m.embed(im))
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"Loaded and ran ({len(scores)} scores)."}
    return {"ok": False, "detail": "Unknown model."}


def download(model_id: str) -> None:
    """Fetch what the model needs (blocking, can take minutes)."""
    if model_id == "faces":
        from ..engines import photo_face_clustering as pf
        pf._ensure_models()
    elif model_id == "looks":
        from ..engines import photo_look_tags as lt
        lt.ensure_models()
    elif model_id == "captions":
        from ..playback.captions import FasterWhisperEngine
        FasterWhisperEngine(model=whisper_name(), cache_dir=_whisper_dir()).prepare()
    else:
        raise ValueError("This model is set up another way; see its hint.")
