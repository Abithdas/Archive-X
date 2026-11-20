"""Send the same state-changing requests to two servers and compare both the answers and what each changed in its database.

    python tools/parity/compare_writes.py \
        --a https://127.0.0.1:18443 --a-data /data/A --b https://127.0.0.1:8443 --b-data /data/B --user admin --password ...

Each server needs its own copy of the same database (build both with tools/parity/seed_fixture.py) and the same login (or, for the
desktop backend, ARCHIVE_X_PARITY_SECRET instead of --user and --password). tools/parity/run_parity.py --writes does all of this.
The requests are in tools/parity/writes.json and run in order against both. After each one the database of each server is
compared: which rows were added and which were removed, per table. Columns that hold times are ignored, because the two
servers write at slightly different moments. Exit code 0 only when every answer and every change is identical.
"""
import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import Client, DEFAULT_IGNORE, diff, scrub  # noqa: E402

# Tables that change on their own (logs, caches, sessions, job bookkeeping) and say nothing about the request.
SKIP_TABLES = {"web_sessions", "app_logs", "logs", "job_events", "jobs", "job_activity", "job_results", "job_transfer_telemetry", "observability_events",
               "operation_log", "watchdog_events", "resource_events", "events",
               # background bookkeeping that lands at a random moment (the caption sync inserted one row into one server in one run in ten)
               "telegram_caption_sync"}
SKIP_SUFFIXES = ("_cache", "_log", "_logs", "_telemetry", "_events")


def scrub_row(columns, row, data_dir=""):
    keep = []
    for name, value in zip(columns, row):
        if data_dir and isinstance(value, str):
            value = value.replace(data_dir, "<DATA>")   # each server has its own data folder; the rows should still match
        n = name.lower()
        if n.endswith(("_at", "_time", "_ts")) or n in {"updated", "created", "timestamp", "ts", "expires", "last_seen", "date"}:
            continue
        keep.append(value)
    return tuple(keep)


def snapshot(path):
    data_dir = str(Path(path).parent.parent)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    out = {}
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        if t in SKIP_TABLES or t.endswith(SKIP_SUFFIXES):
            continue
        cur = conn.execute(f'SELECT * FROM "{t}"')
        cols = [d[0] for d in cur.description]
        out[t] = collections.Counter(scrub_row(cols, r, data_dir) for r in cur.fetchall())
    conn.close()
    return out


def delta(before, after):
    """Per table: the rows that appeared and the rows that went away."""
    d = {}
    for t in after:
        added = after[t] - before.get(t, collections.Counter())
        removed = before.get(t, collections.Counter()) - after[t]
        if added or removed:
            d[t] = (sorted(map(repr, added.elements())), sorted(map(repr, removed.elements())))
    return d


def send(client, scenario):
    headers = {"Content-Type": "application/json", "Origin": scenario.get("origin") or client.base}
    body = scenario["raw"] if "raw" in scenario else json.dumps(scenario.get("body", {}))
    status, hdrs, data = client.request(scenario["method"], scenario["path"], body, headers)
    ctype = dict((k.lower(), v) for k, v in hdrs).get("content-type", "").split(";")[0]
    try:
        parsed = json.loads(data) if ctype == "application/json" else data.decode("utf-8", "replace")
    except ValueError:
        parsed = data.decode("utf-8", "replace")
    return status, ctype, parsed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True)
    ap.add_argument("--a-data", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--b-data", required=True)
    ap.add_argument("--user", default="", help="sign in with this login; without it, ARCHIVE_X_PARITY_SECRET (the desktop launch secret) is used")
    ap.add_argument("--password", default="")
    ap.add_argument("--only", help="run only scenarios whose name contains this text")
    args = ap.parse_args()
    a, b = Client(args.a, args.user, args.password), Client(args.b, args.user, args.password)
    db_a, db_b = Path(args.a_data) / "database" / "archive.db", Path(args.b_data) / "database" / "archive.db"
    scenarios = json.loads((Path(__file__).with_name("writes.json")).read_text())
    if args.only:
        scenarios = [s for s in scenarios if args.only in s["name"]]
    snap_a, snap_b = snapshot(db_a), snapshot(db_b)
    bad = 0
    changed_something = 0
    for s in scenarios:
        ra, rb = send(a, s), send(b, s)
        problems = []
        if ra[0] != rb[0]:
            problems.append(f"status {ra[0]} vs {rb[0]}")
        elif ra[1] != rb[1]:
            problems.append(f"content-type {ra[1]!r} vs {rb[1]!r}")
        elif isinstance(ra[2], (dict, list)) and isinstance(rb[2], (dict, list)):
            problems += diff(scrub(ra[2], DEFAULT_IGNORE), scrub(rb[2], DEFAULT_IGNORE))[:3]
        elif ra[2] != rb[2]:
            problems.append("text bodies differ")
        new_a, new_b = snapshot(db_a), snapshot(db_b)
        da, dbb = delta(snap_a, new_a), delta(snap_b, new_b)
        if da or dbb:
            changed_something += 1
        if da != dbb:
            for t in sorted(set(da) | set(dbb)):
                if da.get(t) != dbb.get(t):
                    problems.append(f"table {t}: A added {len(da.get(t, ([], []))[0])} / removed {len(da.get(t, ([], []))[1])} rows, B added {len(dbb.get(t, ([], []))[0])} / removed {len(dbb.get(t, ([], []))[1])}")
        snap_a, snap_b = new_a, new_b
        rows = sum(len(x[0]) + len(x[1]) for x in da.values())
        tag = f"{ra[0]}, {rows} row change(s) in {len(da)} table(s)"
        if problems:
            bad += 1
            print(f"DIFF  {s['name']} [{tag}]\n      " + "\n      ".join(problems))
        else:
            print(f"same  {s['name']} [{tag}]")
    print(f"\n{len(scenarios) - bad} identical, {bad} different; {changed_something} of {len(scenarios)} requests changed the database")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
