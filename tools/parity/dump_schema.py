"""Write the database schema the Python backend creates, as JSON.

    python tools/parity/dump_schema.py            # rewrite rust/crates/ax-db/schema/schema.json
    python tools/parity/dump_schema.py --check    # exit 1 if the committed snapshot is out of date

The Rust code reads the same database, so any change to a table must be looked at on the Rust side too: the snapshot
changes, the check fails, and the diff shows exactly which tables and columns moved.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SNAPSHOT = ROOT / "rust/crates/ax-db/schema/schema.json"


def current_schema() -> dict:
    from telegram_archive.core.database import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "schema.db")
        # The web sessions table is created by the session store, not by the schema code.
        from telegram_archive.core.auth import SessionStore
        SessionStore(db)
        schema = {}
        with db.lock:
            tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for t in tables:
                schema[t] = [{"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])} for c in db.conn.execute(f'PRAGMA table_info("{t}")')]
        return schema


def main() -> int:
    text = json.dumps(current_schema(), indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not SNAPSHOT.exists() or SNAPSHOT.read_text() != text:
            print("schema snapshot is out of date: run python tools/parity/dump_schema.py and review the diff (the Rust code reads these tables)")
            return 1
        print("schema snapshot is current")
        return 0
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(text)
    print(f"wrote {SNAPSHOT.relative_to(ROOT)}: {len(json.loads(text))} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
