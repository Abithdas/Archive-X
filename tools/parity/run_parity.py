"""One command that proves the Rust front door answers like the Python backend.

    python tools/parity/run_parity.py [--ax-server target/release/ax-server] [--native-routes all] [--keep]

What it does (the "recording harness" of docs/rust-migration-plan.md, Phase 0):

1. Makes a fresh data folder and starts the Python backend in desktop mode on a fixed port with a throwaway launch secret.
2. Fills it with the deterministic fixture (`seed_fixture.py`): channels, videos, performers, photos, face groups.
3. Starts `ax-server` in front of a second copy of that backend, with the native routes switched on.
4. Runs `compare.py` for server A (Python straight) against server B (the front door): every GET route of the OpenAPI file, the
   discovered routes with ids, and the list query variants. Status codes and JSON bodies must match.
5. Runs it twice, once in SFW and once in NSFW mode (the fixture's rows are NSFW). Then `compare_writes.py` for the routes that change data, when `--writes` is given.

With `--self-check` the same two Python copies are compared with each other instead (no Rust needed): this proves the recording itself
is repeatable, which has to be true before a difference means anything.

Exit code 0 only when nothing differs. The scratch data is removed afterwards unless `--keep` is given.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SECRET = "parity-" + "0123456789abcdef" * 4
READY = "ARCHIVE_X_READY "
# Routes whose answer is the app's own activity log or a report built from it: two servers never have the same log.
SKIP = ("/api/exit,/openapi.json,/api/diagnostics/metrics,/api/diagnostics/metrics/export,/api/diagnostics/report,/api/diagnostics/summary,"
        "/api/log/download,/api/diagnostics/logs,/api/diagnostics/report/html")
# Values that differ between two healthy copies of the same fixture: memory and disk figures, per-launch ids, change counters, byte totals.
VOLATILE = "available_bytes,available_gb,free_bytes,launch_id,data_versions,bytes,first,last,recent_degraded,playback_log_tail,recent_playback_events,free_gb,events"


def start_python_backend(data_dir: Path, log_path: Path) -> tuple[subprocess.Popen, int]:
    env = dict(os.environ, ARCHIVE_X_DESKTOP="1", ARCHIVE_X_LAUNCH_SECRET=SECRET, ARCHIVE_X_DATA_DIR=str(data_dir),
               ARCHIVE_X_DESKTOP_PORT="0", ARCHIVE_X_PARENT_PID=str(os.getpid()), PYTHONUNBUFFERED="1", ARCHIVE_X_AUTO_OPEN_BROWSER="0")
    log = open(log_path, "w")
    proc = subprocess.Popen([sys.executable, "-m", "telegram_archive"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 90
    port = 0
    assert proc.stdout is not None
    while time.time() < deadline and not port:
        line = proc.stdout.readline()
        if not line:
            break
        log.write(line)
        if line.startswith(READY):
            port = int(json.loads(line[len(READY):])["port"])
    if not port:
        proc.kill()
        sys.exit(f"the Python backend did not say it was ready; see {log_path}")
    # keep draining so the pipe never blocks the backend
    import threading
    threading.Thread(target=lambda: [log.write(x) for x in proc.stdout], daemon=True).start()
    return proc, port


def start_front_door(exe: Path, backend_port: int, db_path: Path, native: str, dist: Path) -> tuple[subprocess.Popen, int]:
    env = dict(os.environ, ARCHIVE_X_PROXY_KEY="proxy-" + "fedcba9876543210" * 2, ARCHIVE_X_LAUNCH_SECRET=SECRET)
    proc = subprocess.Popen([str(exe), "--listen", "127.0.0.1:0", "--upstream", f"127.0.0.1:{backend_port}", "--upstream-plain", "--print-ready",
                             "--db", str(db_path), "--native-routes", native, "--dist", str(dist)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith(READY):
            return proc, int(json.loads(line[len(READY):])["port"])
    proc.kill()
    sys.exit("ax-server did not say it was ready")


def run(cmd: list[str], env_extra: dict | None = None) -> int:
    print("\n$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT, env=dict(os.environ, **(env_extra or {})))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ax-server", default=str(ROOT / "target" / "release" / "ax-server"), help="the front door binary (cargo build --release -p ax-server)")
    ap.add_argument("--native-routes", default="all")
    ap.add_argument("--dist", default=str(ROOT / "apps" / "web" / "dist"))
    ap.add_argument("--self-check", action="store_true", help="compare two Python copies with each other (no Rust)")
    ap.add_argument("--writes", action="store_true", help="also run compare_writes.py")
    ap.add_argument("--keep", action="store_true", help="keep the scratch data folders")
    args = ap.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="ax-parity-"))
    procs: list[subprocess.Popen] = []
    try:
        ports, dbs = [], []
        for name in ("a", "b"):
            data = scratch / name
            data.mkdir()
            proc, port = start_python_backend(data, scratch / f"{name}.log")
            procs.append(proc)
            code = run([sys.executable, str(HERE / "seed_fixture.py"), "--data-dir", str(data)])
            if code:
                sys.exit(f"seeding {name} failed")
            ports.append(port)
            dbs.append(data / "database" / "archive.db")
        a = f"http://127.0.0.1:{ports[0]}"
        if args.self_check:
            b = f"http://127.0.0.1:{ports[1]}"
        else:
            exe = Path(args.ax_server)
            if not exe.exists():
                sys.exit(f"{exe} not found: build it with `cargo build --release -p ax-server`, or use --self-check")
            dist = Path(args.dist)
            if not dist.exists():
                dist = scratch / "dist"
                dist.mkdir()
            fd, fport = start_front_door(exe, ports[1], dbs[1], args.native_routes, dist)
            procs.append(fd)
            b = f"http://127.0.0.1:{fport}"
        env = {"ARCHIVE_X_PARITY_SECRET": SECRET}
        code = 0
        # SFW first (what the app starts in: the fixture's rows are NSFW, so this pass checks that nothing leaks), then NSFW (where the
        # data is visible and the id-based routes are exercised).
        for mode in ("sfw", "nsfw"):
            code |= run([sys.executable, str(HERE / "compare.py"), "--a", a, "--b", b, "--set-mode", mode, "--discover", "--queries", "--ignore", VOLATILE, "--skip", SKIP,
                         "--path-a", str(scratch / "a"), "--path-b", str(scratch / "b")], env)
        if args.writes and not code:
            code = run([sys.executable, str(HERE / "compare_writes.py"), "--a", a, "--b", b], env)
        sys.exit(code)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        if args.keep:
            print("scratch data kept in", scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
