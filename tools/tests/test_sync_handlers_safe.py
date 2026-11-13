"""A plain `def` route handler runs in a worker thread, where there is no event loop.
It must not call anything that starts or cancels asyncio tasks (that raised "no running event loop" in testing, and
cancelling a task from another thread is not safe)."""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "telegram_archive"
sys.path.insert(0, str(ROOT.parent))
LOOP_ONLY = re.compile(r"asyncio\.(create_task|ensure_future|get_running_loop|get_event_loop)|\.create_task\(|\blive\.cancel\(\)|task\.cancel\(\)|\.call_soon\(")
ROUTE = {"get", "post", "put", "delete", "patch"}


def _calls(fn):
    for x in ast.walk(fn):
        if isinstance(x, ast.Call):
            yield x.func.attr if isinstance(x.func, ast.Attribute) else x.func.id if isinstance(x.func, ast.Name) else ""


def test_sync_route_handlers_do_not_need_the_event_loop():
    loop_only = set()
    for f in ROOT.rglob("*.py"):
        src = f.read_text(errors="ignore")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.FunctionDef) and LOOP_ONLY.search(ast.get_source_segment(src, n) or ""):
                loop_only.add(n.name)
    bad = []
    for f in (ROOT / "web").glob("routes*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.FunctionDef) and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE for d in n.decorator_list):
                hit = sorted(set(_calls(n)) & loop_only - {"stop"})   # RUNNER.stop and the watchdog stop() only use call_soon_threadsafe
                if hit:
                    bad.append(f"{f.name}:{n.name} calls {hit}")
    assert not bad, "sync handlers needing the event loop:\n" + "\n".join(bad)
