"""Record what Python's `ai/ai_knowledge.py` does with a real SQLite database, for `crates/ax-ai/tests/notebook.rs`.

    python tools/parity/gen_ai_notebook_vectors.py          # rewrite crates/ax-ai/tests/notebook_cases.json
    python tools/parity/gen_ai_notebook_vectors.py --check  # fail if the file is out of date

`Findings` (the assistant's notebook) and `Knowledge` (the index of the changelog and the docs, and its search) run on a database made from the fresh
schema (`crates/ax-db/schema/schema.sql`), with a clock that counts, over a folder of files the case lays out (names, text, sizes, modification
times). After each case the three tables it writes (`ai_findings`, `ai_knowledge`, `settings`) are dumped; the Rust port must return the same answers and leave the
same rows. Modification times are whole multiples of 100 ns, the finest step Windows keeps, so the Windows run reads back what was set. The code docstring pieces of `Knowledge` (Python's `ast`) are not ported, so the cases have no Python files in the package folder.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-ai" / "tests" / "notebook_cases.json"
SCHEMA = ROOT / "crates" / "ax-db" / "schema" / "schema.sql"

from telegram_archive.ai import ai_knowledge as K  # noqa: E731,E402


class FakeDb:
    def __init__(self):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA.read_text())

    def setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        self.conn.commit()


def lay_out(base: Path, files: dict):
    folders = []
    for rel, what in files.items():
        path = base / rel
        if what.get("dir"):
            path.mkdir(parents=True, exist_ok=True)
            folders.append((path, what))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if "hex" in what:
            path.write_bytes(bytes.fromhex(what["hex"]))
        elif "fill" in what:
            path.write_bytes((what["fill"] * (what["size"] // len(what["fill"]) + 1))[: what["size"]].encode())
        else:
            path.write_text(what["text"], encoding="utf-8", newline="")
        if "mtime_ns" in what:
            os.utime(path, ns=(what["mtime_ns"], what["mtime_ns"]))
    # a folder that matches *.md is listed too, so its time is part of the fingerprint: set it last, after its files were written
    for path, what in folders:
        if "mtime_ns" in what:
            os.utime(path, ns=(what["mtime_ns"], what["mtime_ns"]))


def dump(db):
    tables = {}
    for t, cols in (("ai_findings", "id,kind,title,title_key,detail,status,created_at,updated_at"), ("ai_knowledge", "id,source,ref,title,body,updated_at"), ("settings", "key,value")):
        tables[t] = [list(r) for r in db.conn.execute(f"SELECT {cols} FROM {t} ORDER BY 1")]
    return tables


def run(case: dict) -> dict:
    db = FakeDb()
    tick = [0]

    def now():
        tick[0] += 1
        return f"2031-01-01T00:{tick[0] // 60:02d}:{tick[0] % 60:02d}+00:00"

    K._now = now
    steps = []
    with tempfile.TemporaryDirectory(prefix="axnb-") as tmp:
        base = Path(tmp)
        lay_out(base, case.get("files", {}))
        K.ROOT = base
        K.PACKAGE = base / "no-package-folder"
        kb, nb = K.Knowledge(db), K.Findings(db)
        for op in case["ops"]:
            k = op["op"]
            try:
                if k == "record":
                    res = nb.record(op["kind"], op["title"], op["detail"])
                elif k == "list":
                    res = nb.list(op["status"], op["limit"])
                elif k == "decide":
                    res = nb.decide(op["id"], op["status"])
                elif k == "counts":
                    res = nb.counts()
                elif k == "ensure":
                    res = kb.ensure()
                elif k == "rebuild":
                    res = kb.rebuild()
                elif k == "kcounts":
                    res = kb.counts()
                elif k == "search":
                    res = kb.search(op["query"], op["limit"])
                elif k == "files":
                    lay_out(base, op["files"])
                    res = None
                elif k == "remove":
                    (base / op["path"]).unlink()
                    res = None
                else:
                    raise SystemExit(k)
                steps.append({"ok": res})
            except ValueError as exc:
                steps.append({"error": str(exc)})
    return {**case, "expect": {"steps": steps, "tables": dump(db)}}


# ------------------------------------------------------------------ cases
def rec(title, kind="issue", detail=""):
    return {"op": "record", "kind": kind, "title": title, "detail": detail}


def cases():
    out = []
    out.append({"name": "records and repeats", "ops": [rec("Thumbnails are not made for new videos", "issue", "seen in the log twice"), rec("Thumbnails are not made for new videos", "issue", "now three times"),
                                                      rec("Add a dark mode idea", "idea"), rec("A plain note about the weather", "note", "x"), {"op": "list", "status": None, "limit": 50},
                                                      {"op": "list", "status": "OPEN", "limit": 2}, {"op": "counts"}]})
    titles = ["", "   ", "abc", "abcde", "abcdef", "!!!!!!!!!!", "Ünïcode tïtle here", "Tab\tand\nnewline words", "\x1c separated \x1f words", "x" * 200, "word " * 60,
              "api_key=" + "s" * 20 + " is leaking", "a" * 119 + " b" + "c" * 30, "İstanbul problem report", "Kelvin sign title", "ß straße title", "  padded   title   words  ", "UPPER lower MiXeD title",
              "UPPER   lower mixed TITLE", "1234567", "a_b_c_d_e_f", "😀 emoji title with words"]
    out.append({"name": "titles", "ops": [rec(t) for t in titles] + [{"op": "list", "status": None, "limit": 200}]})
    out.append({"name": "kinds", "ops": [rec("A title number " + str(i), k, "d") for i, k in enumerate(["issue", "IDEA", "Note", "fix", "", None, "ISSUE", "idea ", "İssue"])] + [{"op": "list", "status": None, "limit": 50}]})
    out.append({"name": "details", "ops": [rec("Detail test one", "issue", ""), rec("Detail test two", "issue", "g" * 1500), rec("Detail test three", "issue", "token=" + "t" * 15 + " seen"), {"op": "list", "status": None, "limit": 50}, rec("Detail test one", "issue", "later detail"),
                                          rec("Detail test one", "issue", ""), rec("Detail test two", "issue", None), {"op": "list", "status": None, "limit": 50}]})
    out.append({"name": "a rejected finding is not written again", "ops": [rec("Rejected idea about colours"), {"op": "decide", "id": 1, "status": "REJECTED"}, rec("Rejected idea about colours", "idea", "again"), rec("rejected IDEA about colours!!"),
                                                                           rec("Accepted problem with sorting"), {"op": "decide", "id": 2, "status": "ACCEPTED"}, rec("Accepted problem with sorting", "issue", "new detail"),
                                                                           {"op": "decide", "id": 2, "status": "FIXED"}, rec("Accepted problem with sorting"), {"op": "counts"}, {"op": "list", "status": "REJECTED", "limit": 5}]})
    full = [rec(f"Open finding number {i:03d}") for i in range(200)]
    out.append({"name": "a full notebook", "ops": full + [rec("One more finding than fits"), rec("Open finding number 007", "issue", "a repeat is still welcome"), {"op": "decide", "id": 3, "status": "FIXED"},
                                                          rec("One more finding than fits"), rec("Yet another one after that"), {"op": "counts"}, {"op": "list", "status": "OPEN", "limit": 3}, {"op": "list", "status": None, "limit": 500}]})
    out.append({"name": "deciding", "ops": [rec("Something worth deciding about"), {"op": "decide", "id": 1, "status": "BOGUS"}, {"op": "decide", "id": 1, "status": "open"}, {"op": "decide", "id": 99, "status": "FIXED"},
                                           {"op": "decide", "id": 1, "status": "ACCEPTED"}, {"op": "decide", "id": 1, "status": "OPEN"}, {"op": "decide", "id": 0, "status": "OPEN"}, {"op": "counts"}]})
    out.append({"name": "listing", "ops": [rec(f"Listing finding {i:02d}", "issue", "d") for i in range(12)] + [{"op": "decide", "id": 3, "status": "FIXED"}, {"op": "decide", "id": 5, "status": "FIXED"}] +
                [{"op": "list", "status": s, "limit": n} for s in (None, "FIXED", "OPEN", "BOGUS", "fixed", "") for n in (0, -5, 1, 4, 300)]})

    changelog = "# Changelog\n\nPreface line before any release.\n- A bullet before any section\n\n## 2.0.0 (2031-01-01)\n\n- First bullet of two.\n  continued on a second line\n\n  and a third after a blank\n- Second   bullet with   extra  spaces\n- ---- dashes then text\n- " + "L" * 300 + "\n- Body " + "q" * 1000 + "\n## 1.9.0\n- Older thing with api_key=" + "k" * 12 + " in it\n\n##\n- no space after hashes belongs to the older section\n## Third section   \n-\nnot a bullet\n- last one with a line separator and another\n"
    base_files = {"CHANGELOG.md": {"text": changelog, "mtime_ns": 1_700_000_000_000_000_100}, "README.md": {"text": "Intro text of the readme.\n# Using Archive X\nOpen the app and click Library.\n## Settings\nChange the theme in Settings.\n", "mtime_ns": 1_700_000_000_000_000_200},
                  "docs/guide.md": {"text": "# Guide\nStart here: scanning folders.\n##   Scanning   \nPick a folder, press Scan.\n#\nuntitled heading body\n###### \nanother untitled\n", "mtime_ns": 1_700_000_000_000_000_300},
                  "docs/sub/deep/notes.md": {"text": "no heading at all, just words about thumbnails\nand more words\n", "mtime_ns": 1_700_000_000_000_000_400},
                  "docs/archive/old.md": {"text": "# Old\nshould be skipped\n", "mtime_ns": 1_700_000_000_000_000_500}, "docs/other/archive/kept.md": {"text": "# Kept\nbecause archive is not first\n", "mtime_ns": 1_700_000_000_000_000_600},
                  "docs/notes.txt": {"text": "not markdown", "mtime_ns": 1_700_000_000_000_000_700}, "docs/UPPER.MD": {"text": "# Upper\nnot matched\n", "mtime_ns": 1_700_000_000_000_000_800}}
    out.append({"name": "build, rebuild and change", "files": base_files, "ops": [{"op": "kcounts"}, {"op": "ensure"}, {"op": "kcounts"}, {"op": "ensure"}, {"op": "search", "query": "scanning folders", "limit": 6},
                                                                                {"op": "files", "files": {"docs/guide.md": {"text": "# Guide\nChanged text about scanning.\n", "mtime_ns": 1_700_000_000_000_009_900}}}, {"op": "ensure"}, {"op": "ensure"},
                                                                                {"op": "rebuild"}, {"op": "remove", "path": "docs/guide.md"}, {"op": "ensure"}, {"op": "kcounts"}, {"op": "ensure"}]})
    sizes = {"docs/exact.md": {"fill": "word ", "size": 300_000, "mtime_ns": 1_700_000_000_000_001_000}, "docs/over.md": {"fill": "word ", "size": 300_001, "mtime_ns": 1_700_000_000_000_001_100},
             "docs/d.md/inner.md": {"text": "# Inner\ninside a folder named like a file\n", "mtime_ns": 1_700_000_000_000_001_200}, "docs/d.md": {"dir": True, "mtime_ns": 1_700_000_000_000_005_000}, "docs/.hidden.md": {"text": "# Hidden\nhidden file\n", "mtime_ns": 1_700_000_000_000_001_300},
             "docs/notes.md.txt": {"text": "# nope\n", "mtime_ns": 1_700_000_000_000_001_400}, "docs/é ü.md": {"text": "# Ünï\nnon-ASCII file name\n", "mtime_ns": 1_700_000_000_000_001_500},
             "docs/a.md": {"text": "# A\nfirst\n", "mtime_ns": 1_700_000_000_000_001_600}, "docs/B.md": {"text": "# B\nsecond\n", "mtime_ns": 1_700_000_000_000_001_700}, "docs/b/c.md": {"text": "# C\nthird\n", "mtime_ns": 1_700_000_000_000_001_800}}
    out.append({"name": "which files count", "files": sizes, "ops": [{"op": "ensure"}, {"op": "kcounts"}]})
    odd = {"CHANGELOG.md": {"hex": "2323204f6c6420322e300a2d2062756c6c657420e282206d69737369ng".replace("ng", "6e") + "0a2d20ff66666620746f6f0a", "mtime_ns": 1_700_000_000_000_002_000},
           "README.md": {"text": "head\r\n# Title one\r\nbody one\rbody two\x0bbody three\x1cbody four\x85body five\n# Title two body six\n", "mtime_ns": 1_700_000_000_000_002_100},
           "docs/empty.md": {"text": "", "mtime_ns": 1_700_000_000_000_002_200}, "docs/headings.md": {"text": "# one\n# two\n# three\ntext\n# four\n", "mtime_ns": 1_700_000_000_000_002_300},
           "docs/long.md": {"text": "# " + "T" * 200 + "\n" + "B" * 1500 + "\n", "mtime_ns": 1_700_000_000_000_002_400}, "docs/bad.md": {"hex": "23206261640a6162ff63ffffe28264", "mtime_ns": 1_700_000_000_000_002_500}}
    out.append({"name": "odd text", "files": odd, "ops": [{"op": "ensure"}, {"op": "kcounts"}]})
    many = "# Changelog\n\n## 1.0\n" + "".join(f"- alpha item {i:03d} beta\n" for i in range(450)) + "".join(f"- gamma item {i}\n" for i in range(5))
    docs = {"CHANGELOG.md": {"text": many, "mtime_ns": 1_700_000_000_000_003_000},
            "docs/rank.md": {"text": "# Thumbnails\nThumbnails are made when a video is scanned. Thumbnail thumbnail thumbnail thumbnail thumbnail.\n# Scanning\nScanning folders finds videos and makes thumbnails.\n# Other\nunrelated words about settings and the theme\n"
                                    + "# Window\n" + "İ" * 200 + " the word zebra appears after a long run of dotted capitals so the window shifts " + "z" * 10 + "\n", "mtime_ns": 1_700_000_000_000_003_100},
            "docs/under.md": {"text": "# Under_score words\nsnake_case_name and another_name are searched with a wildcard underscore\n", "mtime_ns": 1_700_000_000_000_003_200}}
    queries = ["", "the a an", "a", "x y z", "thumbnails", "Thumbnails THUMBNAILS", "scanning thumbnails folders", "alpha", "alpha beta gamma", "gamma", "snake_case_name", "snake_case", "s_ake", "zebra", "settings theme",
               "one two three four five six seven eight nine ten", "the thumbnails and the scanning", "ÜNÏCODE", "nothing-matches-this", "how do I scan a folder", "items item"]
    out.append({"name": "searching", "files": docs, "ops": [{"op": "ensure"}] + [{"op": "search", "query": q, "limit": n} for q in queries for n in (6, 1)] +
                [{"op": "search", "query": "thumbnails", "limit": n} for n in (0, -3, 3, 10, 11, 50, None)]})
    out.append({"name": "searching an empty index", "ops": [{"op": "search", "query": "anything", "limit": 6}, {"op": "ensure"}, {"op": "kcounts"}, {"op": "search", "query": "anything", "limit": 6}, {"op": "ensure"}, {"op": "ensure"}]})
    out.append({"name": "an index with nothing in it is rebuilt each time", "files": {"docs/empty.md": {"text": "", "mtime_ns": 1_700_000_000_000_004_000}}, "ops": [{"op": "ensure"}, {"op": "ensure"}, {"op": "kcounts"}]})
    # the 400th and the 401st rows are the limit of what is scored
    for n, label in ((400, "400th"), (401, "401st")):
        rows = [f"- alpha item {i:03d} beta\n" for i in range(n - 1)] + ["- alpha alpha alpha alpha alpha the very best match\n"] + [f"- alpha tail {i}\n" for i in range(60)]
        out.append({"name": f"the best match is the {label} row", "files": {"CHANGELOG.md": {"text": "## 1.0\n" + "".join(rows), "mtime_ns": 1_700_000_000_000_004_100}},
                    "ops": [{"op": "ensure"}, {"op": "search", "query": "alpha", "limit": 3}, {"op": "search", "query": "alpha", "limit": 11}, {"op": "search", "query": "alpha", "limit": 50}]})
    # scoring: how often a word counts, and what a title is worth
    score = "## 1.0\n" + "".join(f"- {' '.join(['omega'] * n)} counted {n} times\n" for n in (1, 4, 5, 6, 8)) + "- plain filler bullet\n"
    out.append({"name": "how often a word counts", "files": {"CHANGELOG.md": {"text": score, "mtime_ns": 1_700_000_000_000_004_200}}, "ops": [{"op": "ensure"}, {"op": "search", "query": "omega", "limit": 10}]})
    titled = "# zorbit\nnothing here\n# Other\nzorbit zorbit zorbit and more\n# Third\nzorbit once\n"
    out.append({"name": "what a title is worth", "files": {"docs/titles.md": {"text": titled, "mtime_ns": 1_700_000_000_000_004_300}}, "ops": [{"op": "ensure"}, {"op": "search", "query": "zorbit", "limit": 10}]})
    out.append({"name": "a docs file called CHANGELOG", "files": {"docs/CHANGELOG.md": {"text": "# Docs changelog\n- a bullet in the docs copy\n", "mtime_ns": 1_700_000_000_000_004_400},
                                                            "CHANGELOG.md": {"text": "## 1.0\n- a bullet in the real one\n", "mtime_ns": 1_700_000_000_000_004_500}}, "ops": [{"op": "ensure"}, {"op": "kcounts"}, {"op": "search", "query": "bullet", "limit": 10}]})
    filler = "lorem ipsum dolor sit amet " * 30
    window = {"docs/window.md": {"text": "# Window\n" + filler + "zebra marks the spot " + filler + "\n# Dotted\n" + "\u0130\u0130\u0130 " + filler + "quokka lives here " + filler + "\n# Short\nzebra at the very start\n", "mtime_ns": 1_700_000_000_000_004_600}}
    out.append({"name": "the part of the text that is shown", "files": window, "ops": [{"op": "ensure"}, {"op": "search", "query": "zebra", "limit": 5}, {"op": "search", "query": "quokka", "limit": 5}, {"op": "search", "query": "zebra quokka", "limit": 5}]})
    short = {"docs/short.md": {"text": "# UI\nthe ui is simple\n# Other\nsome other text\n", "mtime_ns": 1_700_000_000_000_004_700},
             "CHANGELOG.md": {"text": "## 1.0\n- one two three four five six seven eight gamma only here\n- " + " ".join(["alpha"] * 10) + " then more\n- zxqword stands apart\n", "mtime_ns": 1_700_000_000_000_004_800}}
    out.append({"name": "short words and long questions", "files": short, "ops": [{"op": "ensure"}] + [{"op": "search", "query": q, "limit": 5} for q in
                ["ui", "a ui", "UI is", "one two three four five six seven eight gamma", "one two three four five six seven eight nine gamma", "alpha alpha alpha alpha alpha alpha alpha alpha gamma",
                 "alpha alpha alpha alpha alpha alpha alpha alpha alpha gamma", "gamma alpha", "one two three four five six seven eight zxqword", "one two three four five six seven zxqword", "zxqword"]]})
    return out


def render() -> str:
    return "[\n" + ",\n".join(json.dumps(run(c), ensure_ascii=False) for c in cases()) + "\n]\n"


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is out of date; run python tools/parity/gen_ai_notebook_vectors.py")
            sys.exit(1)
        print("ai notebook vectors are up to date")
        return
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(text) // 1024} KiB")


if __name__ == "__main__":
    main()
