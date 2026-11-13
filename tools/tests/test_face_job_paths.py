import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.engines import photo_face_clustering as pfc


class Row(dict):
    def keys(self):
        return list(super().keys())


def test_uncached_photo_with_telegram_disconnected_gives_the_clear_error(tmp_path, monkeypatch):
    # Regression: a performer-picture block pasted into this function made it fail with NameError (match_people) instead.
    monkeypatch.setattr(pfc, "_stored_photo_path", lambda row: tmp_path / "missing.jpg")
    ctx = types.SimpleNamespace(telegram=types.SimpleNamespace(client=None, connected=False))
    row = Row(channel_id="-1001", message_id=5, filename="a.jpg", local_path="")
    try:
        asyncio.run(pfc._ensure_photo_file(ctx, row))
        raise AssertionError("should raise")
    except RuntimeError as e:
        assert "Telegram is disconnected" in str(e)


def test_analysis_job_names_everything_it_uses():
    # Every name the job reads must be defined inside it (the summary used `pictures`, which was missing).
    import ast
    src = Path(pfc.__file__).read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("run_face") or isinstance(n, ast.AsyncFunctionDef) and "Analyze unprocessed" in (ast.get_docstring(n) or ""))
    assigned = {t.id for x in ast.walk(fn) for t in (x.targets if isinstance(x, ast.Assign) else [x.target] if isinstance(x, (ast.AnnAssign, ast.AugAssign)) else []) if isinstance(t, ast.Name)}
    assert "pictures" in assigned and "match_people" in assigned and "matched_info" in assigned
