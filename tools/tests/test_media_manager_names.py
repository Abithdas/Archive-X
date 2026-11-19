"""The media manager's fragments are exec()-joined into one module, so ruff's undefined-name check is switched off for them (pyproject).
This test restores it: every name a fragment reads must exist in the joined module after import."""
import ast
import builtins
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
PKG = Path(__file__).resolve().parents[2] / "telegram_archive" / "media_manager"


def _free_names(tree: ast.AST) -> set[str]:
    bound: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
            if not isinstance(n, ast.ClassDef):
                a = n.args
                for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                    if x:
                        bound.add(x.arg)
        elif isinstance(n, ast.Lambda):
            a = n.args
            for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if x:
                    bound.add(x.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                bound.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - bound


def test_every_name_a_fragment_borrows_exists_in_the_host():
    host = importlib.import_module("telegram_archive.media_manager.telegram_media_manager")
    ns = vars(host)
    missing = {}
    for name in host._MM_PARTS:
        tree = ast.parse((PKG / (name + ".py")).read_text(encoding="utf-8"))
        gone = sorted(x for x in _free_names(tree) if x not in ns and not hasattr(builtins, x))
        if gone:
            missing[name] = gone
    assert not missing, missing
