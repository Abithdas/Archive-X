"""Every local AI model Archive X can use, in one list: what it is, what it powers, whether it is ready, a quick self-test, and, for each,
the sizes on offer (recommended first), with download, switch and delete.

No model weights are bundled and none downloads by itself. Everything is fetched when the person asks, checked against a pinned checksum
where one exists, and can be deleted again. The assistant (engine and model) downloads in one step; see model_hub for the plain-file models.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import config

WHISPER_MB = {"tiny": 75, "base": 142, "small": 466, "medium": 1500, "large-v3": 2900, "large-v3-turbo": 1600}
WHISPER_REPOS = {"base": "Systran/faster-whisper-base", "small": "Systran/faster-whisper-small", "medium": "Systran/faster-whisper-medium",
                 "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}
CONTROL: dict[str, Any] = {}   # the live-caption service registers {"get": () -> name, "set": (name) -> None} here (see routes_tracks)


def whisper_name() -> str:
    """The speech model in use: what the live-caption service has (it follows the saved choice), else the environment, else "small"."""
    get = CONTROL.get("get")
    if get:
        try:
            return str(get())
        except Exception:
            pass
    return os.environ.get("ARCHIVE_X_WHISPER_MODEL", "small")


def _whisper_dir() -> Path:
    return Path(config.PLAYER_CACHE_DIR) / "models"


def _whisper_folder(name: str) -> Path:
    repo = WHISPER_REPOS.get(name, f"Systran/faster-whisper-{name}")
    return _whisper_dir() / ("models--" + repo.replace("/", "--"))


def _whisper_cached(name: str | None = None) -> bool:
    folder = _whisper_folder(name or whisper_name())
    return folder.is_dir() and any(folder.rglob("model.bin"))


def _has(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _faces_ready() -> bool:
    """Both face files are there and match their checksums (the one model that is checked in full each time, because it is cheap)."""
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
    from . import model_hub as hub
    return lt.available() and hub.installed("looks", hub.selected("looks"))


def _content_ready() -> bool:
    from ..engines import photo_content_tags as ct
    return ct.available() and ct.model_ready()


def _setup_engines():
    try:
        from . import local_ai_setup
        return local_ai_setup.supported_engines()
    except Exception:
        return []


def _llm_dir() -> Path:
    return Path(config.AI_MODELS_DIR)


def _tiers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recommended size first, then the bigger ones from small to large, then the lighter ones."""
    rec = next((r for r in rows if r["recommended"]), rows[0])
    higher = sorted((r for r in rows if r is not rec and r["size_mb"] >= rec["size_mb"]), key=lambda r: r["size_mb"])
    lighter = sorted((r for r in rows if r is not rec and r["size_mb"] < rec["size_mb"]), key=lambda r: -r["size_mb"])
    return [rec, *higher, *lighter]


def llm_file_of(ai) -> str:
    """File name of the assistant model now in use ('' when there is no assistant)."""
    try:
        return Path(getattr(getattr(ai, "runtime", ai), "model_path")).name if ai is not None else ""
    except Exception:
        return ""


def _llm_variants(llm_file: str) -> list[dict[str, Any]]:
    from . import local_ai_setup as las
    ram = las.memory_gb()
    rec = las.recommend(ram)
    return _tiers([{"id": m.key, "label": m.label, "name": m.model_id, "size_mb": m.size_mb, "note": m.blurb, "recommended": m.key == rec,
                    "installed": (_llm_dir() / m.file).is_file(), "selected": bool(llm_file) and llm_file == m.file,
                    "fits": ram == 0 or ram >= m.min_ram_gb * 0.9} for m in las.MODELS.values()])


def _caption_variants() -> list[dict[str, Any]]:
    from ..playback import captions
    now = whisper_name()
    rows = []
    for name, v in captions.MODELS.items():
        rows.append({"id": name, "label": v["label"], "name": f"Whisper {name}", "size_mb": WHISPER_MB.get(name, 466), "note": v["note"],
                     "recommended": name == captions.DEFAULT_MODEL, "installed": _whisper_cached(name), "selected": name == now, "fits": True})
    return _tiers(rows)


def _file_variants(family: str) -> list[dict[str, Any]]:
    from . import model_hub as hub
    now = hub.selected(family)
    return _tiers([{"id": v.id, "label": v.label, "name": v.label, "size_mb": v.size_mb, "note": v.note, "recommended": v.recommended,
                    "installed": hub.installed(family, v.id), "selected": v.id == now, "fits": True} for v in hub.variants(family)])


def variants_of(family: str, llm_file: str = "") -> list[dict[str, Any]]:
    if family == "llm":
        return _llm_variants(llm_file)
    if family == "captions":
        return _caption_variants()
    return _file_variants(family)


def catalog(ai_status: dict | None = None, llm_file: str = "") -> list[dict[str, Any]]:
    """The five local models with their current state and the sizes on offer (no downloads, no model loading)."""
    rt = (ai_status or {}).get("runtime") or {}
    llm_files = bool((rt.get("runtime") or {}).get("exists")) and bool((rt.get("model") or {}).get("exists"))
    llm_state = "ready" if rt.get("ready") else "stopped" if llm_files else "not_set_up"
    wname = whisper_name()
    cv_ok = _has("cv2")
    faces = _faces_ready()
    looks = _looks_ready()
    ort_ok = _has("onnxruntime")
    content = _content_ready()
    items = [
        {"id": "llm", "name": "Assistant model (llama.cpp)", "detail": "Qwen3 chat model, run by llama.cpp on this PC", "size_mb": 2500, "licence": "Apache-2.0 models, MIT runtime",
         "state": llm_state, "installable": llm_state == "not_set_up" and bool(_setup_engines()), "powers": ["AI Assistance: chat", "Library intelligence", "Diagnostics", "Jobs", "Recommendations"],
         "hint": "Downloads the engine (about 25 MB) and the model once, and checks both." if llm_state == "not_set_up" else "It starts by itself when you ask a question." if llm_state == "stopped" else ""},
        {"id": "faces", "name": "Faces (YuNet + SFace)", "detail": "Finds faces and gives each a fingerprint so the same person can be grouped", "size_mb": 39, "licence": "Apache-2.0 (OpenCV Zoo)",
         "state": "ready" if faces else "needs_package" if not cv_ok else "not_downloaded", "installable": cv_ok and not faces,
         "powers": ["People and Find people", "Check faces", "Matching with performer pictures", "Head finding for look suggestions"],
         "hint": "" if faces else "Needs opencv-python-headless (pip install opencv-python-headless)." if not cv_ok else "Downloads about 39 MB once."},
        {"id": "captions", "name": "Live captions (Whisper)", "detail": "Speech to text in any language, with translation to English", "size_mb": WHISPER_MB.get(wname, 466), "licence": "MIT (faster-whisper)",
         "state": "needs_package" if not _has("faster_whisper") else "ready" if _whisper_cached() else "not_downloaded", "installable": _has("faster_whisper") and not _whisper_cached(),
         "powers": ["Video player: live captions (English or original language)", "Chinese, Japanese and other languages"],
         "hint": "Needs faster-whisper (pip install faster-whisper)." if not _has("faster_whisper") else "" if _whisper_cached() else f"Downloads about {WHISPER_MB.get(wname, 466)} MB once."},
        {"id": "content", "name": "Content tags", "detail": "Finds which body-part regions are visible in a photo", "size_mb": 12, "licence": "AGPL-3.0 weights, downloaded on request",
         "state": "ready" if content else "needs_package" if not (ort_ok and cv_ok) else "not_downloaded", "installable": ort_ok and cv_ok and not content, "powers": ["Photos: Content tags and filters"],
         "hint": "" if content else "Needs onnxruntime and opencv-python-headless." if not (ort_ok and cv_ok) else "Not part of the app: it is an optional download of about 12 MB, under the AGPL-3.0 licence."},
        {"id": "looks", "name": "Look suggestions (CLIP)", "detail": "Suggests hair colour and hair length from the head in a photo", "size_mb": 155, "licence": "MIT (OpenAI CLIP)",
         "state": "needs_package" if not _has("tokenizers") or not ort_ok else "ready" if looks else "not_downloaded", "installable": _has("tokenizers") and ort_ok and not looks,
         "powers": ["Photos: Looks (suggested)"],
         "hint": "Needs onnxruntime and tokenizers (pip install onnxruntime tokenizers)." if not _has("tokenizers") or not ort_ok else "" if looks else "Downloads about 155 MB once (checked against a fixed checksum)."},
    ]
    for m in items:
        vs = variants_of(m["id"], llm_file)
        m["variants"] = vs
        pick = next((v for v in vs if v["selected"]), None) or next((v for v in vs if v["recommended"]), vs[0])
        m["selected"] = pick["id"]
        m["size_mb"] = pick["size_mb"]
        m["freeable_mb"] = round(sum(v["size_mb"] for v in vs if v["installed"] and not v["selected"]))
    return items


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
                return {"ok": False, "detail": "onnxruntime or OpenCV is not installed."}
            if not ct.model_ready():
                return {"ok": False, "detail": "The content model is not downloaded yet."}
            from ..engines.content_detector import ContentDetector
            _blank_image(img)
            dets = ContentDetector(ct.model_path()).detect(str(img))
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"Loaded and ran. {len(dets)} regions in the blank test picture (none expected)."}
        if model_id == "looks":
            from ..engines import photo_look_tags as lt
            from . import model_hub as hub
            if not _looks_ready():
                return {"ok": False, "detail": "The look model is not downloaded yet."}
            from PIL import Image
            _blank_image(img)
            m = lt.LookModel(hub.active_dir("looks"))
            with Image.open(img) as im:
                scores = m.scores(m.embed(im))
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"Loaded and ran ({len(scores)} scores)."}
    return {"ok": False, "detail": "Unknown model."}


def download(model_id: str, variant_id: str | None = None, on_progress=None, cancel=None) -> None:
    """Fetch what a model (the chosen size, or `variant_id`) needs. Blocking, can take minutes. `on_progress(done_bytes, total_bytes)` is called as it goes."""
    from . import model_hub as hub
    if model_id == "captions":
        _download_captions(variant_id or whisper_name(), on_progress)
    elif model_id in {"faces", "content", "looks"}:
        hub.fetch(model_id, variant_id or hub.selected(model_id), on_progress, cancel)
    else:
        raise ValueError("This model is set up another way; see its hint.")


def _download_captions(name: str, on_progress=None) -> None:
    """faster-whisper fetches its own files, so progress is read from the folder it is filling."""
    import threading
    from ..playback.captions import MODELS, FasterWhisperEngine
    if name not in MODELS:
        raise ValueError("Unknown speech model")
    total = WHISPER_MB.get(name, 466) << 20
    stop = threading.Event()

    def poll() -> None:
        from . import model_hub as hub
        while not stop.wait(1.0):
            on_progress(min(hub.dir_bytes(_whisper_folder(name)), total), total)
    t = threading.Thread(target=poll, daemon=True) if on_progress else None
    if t:
        t.start()
    try:
        FasterWhisperEngine(model=name, cache_dir=_whisper_dir()).prepare()
    finally:
        stop.set()


def select_variant(model_id: str, variant_id: str, *, ai=None) -> dict[str, Any]:
    """Use another size from now on. It must already be downloaded (the assistant restarts on it; captions load it on the next window)."""
    from . import model_hub as hub
    rows = {v["id"]: v for v in variants_of(model_id, llm_file_of(ai))}
    if variant_id not in rows:
        raise KeyError(variant_id)
    if not rows[variant_id]["installed"]:
        raise ValueError("Download it first.")
    if model_id == "llm":
        from . import local_ai_setup as las
        m = las.MODELS[variant_id]
        if ai is None:
            raise ValueError("The assistant is not available.")
        ai.stop()
        ai.configure(model_path=str(_llm_dir() / m.file), model_id=m.model_id)
    elif model_id == "captions":
        setter = CONTROL.get("set")
        if setter is None:
            raise ValueError("Live captions are not available.")
        setter(variant_id)
    else:
        hub.select(model_id, variant_id)
    return {"selected": variant_id}


def delete_variant(model_id: str, variant_id: str, *, ai=None) -> int:
    """Delete one downloaded size and return the megabytes freed. The size in use cannot be deleted while another one is downloaded to switch to;
    when it is the only one downloaded it can go (the feature then asks for a download again)."""
    from . import model_hub as hub
    llm_file = llm_file_of(ai) if model_id == "llm" else ""
    rows = {v["id"]: v for v in variants_of(model_id, llm_file)}
    if variant_id not in rows:
        raise KeyError(variant_id)
    row = rows[variant_id]
    if not row["installed"]:
        return 0
    if row["selected"] and any(v["installed"] for k, v in rows.items() if k != variant_id):
        raise ValueError("That size is the one in use. Switch to another size first.")
    if model_id == "llm":
        from . import local_ai_setup as las
        if ai is not None:
            ai.stop()
        path = _llm_dir() / las.MODELS[variant_id].file
        size = path.stat().st_size if path.is_file() else 0
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".part").unlink(missing_ok=True)
        return round(size / 1e6)
    if model_id == "captions":
        return round(hub.rmtree(_whisper_folder(variant_id)) / 1e6)
    return round(hub.remove(model_id, variant_id) / 1e6)
