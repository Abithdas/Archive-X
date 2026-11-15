"""Child-process entry point for native PyAV media operations.

Never call this module directly from the UI. It exists so a native FFmpeg/PyAV
access violation can terminate only this helper process rather than the web
application.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import internal_media


def emit(obj):
    print(json.dumps(obj, separators=(",", ":")), flush=True)


def main() -> int:
    if len(sys.argv) < 3:
        emit({"type": "error", "error": "operation and payload are required"})
        return 2
    op = sys.argv[1]
    payload = json.loads(sys.argv[2])
    if op == "probe":
        result = internal_media._probe_direct(payload["path"])
    elif op == "remux":
        result = internal_media._remux_direct(payload["input_path"], payload["output_path"])
    elif op == "thumbnail":
        result = str(internal_media._make_thumbnail_direct(
            payload["input_path"], payload["output_path"],
            timestamp=payload.get("timestamp"), width=int(payload.get("width") or 1536),
            height=int(payload.get("height") or 864),
        ))
    elif op == "snapshot_frames":
        def progress(value):
            emit({"type": "progress", "value": float(value)})
        result = internal_media._make_snapshot_frames_direct(
            payload["input_path"], payload["output_dir"],
            count=int(payload.get("count") or 36), width=int(payload.get("width") or 640),
            quality=int(payload.get("quality") or 92), on_progress=progress,
        )
    elif op == "transcode":
        def progress(value):
            emit({"type": "progress", "value": float(value)})
        result = internal_media._transcode_direct(
            payload["input_path"], payload["output_path"],
            preserve_video=bool(payload.get("preserve_video", True)), on_progress=progress,
        )
    else:
        emit({"type": "error", "error": f"unknown operation: {op}"})
        return 2
    emit({"type": "result", "value": result})
    return 0


def run() -> None:
    """Run one operation from sys.argv (operation, JSON payload); also what the frozen backend calls for --ax-native-worker."""
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if not exc.code:
            raise                      # a normal finish is not an error
        emit({"type": "worker_error", "operation": sys.argv[1] if len(sys.argv) > 1 else "unknown",
              "error_type": "SystemExit", "error": str(exc.code), "traceback": ""})
        raise
    except BaseException as exc:
        # If Python catches the failure, return a non-zero status. A hard
        # Windows access violation still kills only this child process.
        emit({"type": "worker_error", "operation": sys.argv[1] if len(sys.argv) > 1 else "unknown",
              "error_type": type(exc).__name__, "error": str(exc),
              "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:]})
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    run()
