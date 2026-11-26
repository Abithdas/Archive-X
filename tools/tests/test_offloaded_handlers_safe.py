"""Work a handler moves into a worker thread (`def _work()` + `asyncio.to_thread`) has no event loop there.

It must not start or wake asyncio things (`create_task`, the job queue behind `jobs.submit`, asyncio locks and queues): those need the loop, and
the job queue is not safe to touch from another thread. The handler keeps such calls on the loop, after the thread returns.
"""
import ast
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "telegram_archive" / "web"
NEEDS_LOOP = re.compile(r"\.submit\(|_queue_catalogue_caption_sync|create_task|ensure_future|get_running_loop|get_event_loop|call_soon|put_nowait|asyncio\.(Lock|Event|Queue|Semaphore)")


def test_work_moved_to_a_thread_does_not_need_the_event_loop():
    bad = []
    moved = 0
    for f in WEB.glob("routes*.py"):
        src = f.read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.FunctionDef) and n.name in ("_work", "_verified_ids"):
                moved += 1
                if NEEDS_LOOP.search(ast.get_source_segment(src, n) or ""):
                    bad.append(f"{f.name}:{n.lineno} {n.name}")
    assert moved >= 15, "the moved handlers were expected to be there"
    assert not bad, "moved to a thread but needs the event loop:\n" + "\n".join(bad)
