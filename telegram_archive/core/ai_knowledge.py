"""What the assistant knows about Archive X itself, and the notebook where it keeps what it notices.

Small on purpose (the PC may have 8 GB of RAM shared with the app): no embedding model, just plain SQLite and word scoring.

* **Knowledge** is built from the project's own words: the changelog, the docs, and the docstrings of the program's modules, classes and
  functions (when the source is there). `search()` finds the pieces that mention the words of a question, so the assistant can answer from
  what the application says about itself instead of guessing. It is rebuilt only when those files change.
* **Findings** are its notebook: problems and ideas it noticed, each one you can accept, reject or mark fixed. A finding you rejected is
  never recorded again, so it stops repeating itself, and what you accepted or fixed is what it reads first next time. Writing here is
  harmless (nothing in the application changes) so it needs no permission, but everything is shown to you and can be thrown away.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

from .. import config
from .ai_safety import redact_text
from .swallowed import note as _swallowed_note

ROOT = Path(__file__).resolve().parents[2]            # the repository (or install) folder holding CHANGELOG.md and docs/
PACKAGE = Path(__file__).resolve().parents[1]         # telegram_archive/
MAX_CODE_CHUNKS = 4000
MAX_BODY = 900
FINDING_KINDS = ("issue", "idea", "fix", "note")
STATUSES = ("OPEN", "ACCEPTED", "REJECTED", "FIXED")
MAX_OPEN_FINDINGS = 200
STOP = frozenset("the a an and or of to in on for is are was be it this that with how what why do does did can i my me you your".split())


def _now() -> str:
    return config.now_iso()


# ---------------------------------------------------------------- knowledge -----------------------------------------------------------------------
def _chunks_changelog(path: Path) -> list[tuple[str, str, str, str]]:
    """(source, ref, title, body) for each top-level bullet of each release section."""
    out: list[tuple[str, str, str, str]] = []
    section, bullet = "", []

    def flush() -> None:
        if bullet:
            text = "\n".join(bullet).strip()
            out.append(("changelog", section, re.sub(r"\s+", " ", bullet[0].lstrip("- ").strip())[:110], text[:MAX_BODY]))
            bullet.clear()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            flush()
            section = line[3:].strip()
        elif line.startswith("- "):
            flush()
            bullet.append(line)
        elif bullet and line.strip():
            bullet.append(line.strip())
    flush()
    return out


def _chunks_markdown(path: Path, rel: str) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    title, body = path.stem, []

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            out.append(("docs", rel, title[:110], text[:MAX_BODY + 300]))
        body.clear()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            flush()
            title = line.lstrip("# ").strip() or path.stem
        else:
            body.append(line)
    flush()
    return out


def _chunks_code(path: Path, rel: str) -> list[tuple[str, str, str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    out: list[tuple[str, str, str, str]] = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        out.append(("code", rel, f"module {rel}", module_doc[:MAX_BODY]))

    def visit(node: ast.AST, prefix: str) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                doc = ast.get_docstring(child)
                if doc and not child.name.startswith("__"):
                    args = ""
                    if not isinstance(child, ast.ClassDef):
                        args = "(" + ", ".join(a.arg for a in child.args.args if a.arg != "self")[:80] + ")"
                    out.append(("code", rel, f"{name}{args}", doc[:MAX_BODY]))
                if isinstance(child, ast.ClassDef):
                    visit(child, name + ".")
    visit(tree, "")
    return out


def _source_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for name in ("CHANGELOG.md", "README.md"):
        if (ROOT / name).is_file():
            files.append((name, ROOT / name))
    docs = ROOT / "docs"
    if docs.is_dir():
        for p in sorted(docs.rglob("*.md")):
            if p.stat().st_size <= 300_000 and "archive" not in p.relative_to(docs).parts[:1]:
                files.append((f"docs/{p.relative_to(docs).as_posix()}", p))
    if PACKAGE.is_dir():
        for p in sorted(PACKAGE.rglob("*.py")):
            if "__pycache__" not in p.parts and p.stat().st_size <= 400_000:
                files.append((p.relative_to(PACKAGE).as_posix(), p))
    return files


def _signature(files: list[tuple[str, Path]]) -> str:
    h = hashlib.sha1()
    for rel, p in files:
        st = p.stat()
        h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


class Knowledge:
    def __init__(self, db):
        self.db = db

    def ensure(self) -> bool:
        """Build the index if it is missing or the files changed since it was built. True when it was (re)built."""
        try:
            files = _source_files()
            sig = _signature(files)
        except OSError:
            return False
        with self.db.lock:
            have = int(self.db.conn.execute("SELECT COUNT(*) FROM ai_knowledge").fetchone()[0])
        if have and str(self.db.setting("ai_kb_signature", "") or "") == sig:
            return False
        self.rebuild(files, sig)
        return True

    def rebuild(self, files: list[tuple[str, Path]] | None = None, sig: str | None = None) -> dict[str, int]:
        files = files if files is not None else _source_files()
        rows: list[tuple[str, str, str, str]] = []
        code = 0
        for rel, path in files:
            try:
                if rel.endswith(".py"):
                    if code >= MAX_CODE_CHUNKS:
                        continue
                    found = _chunks_code(path, rel)
                    code += len(found)
                elif rel == "CHANGELOG.md":
                    found = _chunks_changelog(path)
                else:
                    found = _chunks_markdown(path, rel)
                rows.extend(found)
            except OSError:
                _swallowed_note("ai_knowledge.rebuild")
        stamp = _now()
        with self.db.lock:
            self.db.conn.execute("DELETE FROM ai_knowledge")
            self.db.conn.executemany("INSERT INTO ai_knowledge(source,ref,title,body,updated_at) VALUES (?,?,?,?,?)", [(s, r, redact_text(t), redact_text(b), stamp) for s, r, t, b in rows])
            self.db.conn.commit()
        self.db.set_setting("ai_kb_signature", sig if sig is not None else _signature(files))
        return self.counts()

    def counts(self) -> dict[str, int]:
        with self.db.lock:
            return {r[0]: int(r[1]) for r in self.db.conn.execute("SELECT source,COUNT(*) FROM ai_knowledge GROUP BY source").fetchall()}

    def search(self, query: str, limit: int = 6) -> list[dict[str, str]]:
        """The pieces of Archive X's own text that mention the words of `query`, best first."""
        words = [w for w in dict.fromkeys(re.findall(r"[a-z0-9_]{2,}", str(query or "").lower())) if w not in STOP][:8]
        if not words:
            return []
        clauses = " OR ".join("LOWER(title) LIKE ? OR LOWER(body) LIKE ?" for _ in words)
        params: list[Any] = []
        for w in words:
            params += [f"%{w}%", f"%{w}%"]
        with self.db.lock:
            rows = self.db.conn.execute(f"SELECT source,ref,title,body FROM ai_knowledge WHERE {clauses} LIMIT 400", params).fetchall()
        scored = []
        for source, ref, title, body in rows:
            tl, bl = title.lower(), body.lower()
            score = sum((3 if w in tl else 0) + min(4, bl.count(w)) for w in words) + (1 if source != "code" else 0)
            hits = sum(1 for w in words if w in tl or w in bl)
            scored.append((hits, score, source, ref, title, body))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        out = []
        for _hits, _score, source, ref, title, body in scored[:max(1, min(10, int(limit or 6)))]:
            low = body.lower()
            at = min((i for i in (low.find(w) for w in words) if i >= 0), default=0)
            start = max(0, at - 80)
            out.append({"source": source, "where": ref, "title": title, "text": body[start:start + 320].strip()})
        return out


# ---------------------------------------------------------------- findings ------------------------------------------------------------------------
def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()[:120]


class Findings:
    def __init__(self, db):
        self.db = db

    def record(self, kind: str, title: str, detail: str = "") -> dict[str, Any]:
        """Write a problem or idea in the notebook. A rejected one is not written again; a repeat only refreshes the detail."""
        t = redact_text(" ".join(str(title or "").split()))[:140]
        key = _key(t)
        if len(key) < 6:
            return {"recorded": False, "reason": "A finding needs a clear title."}
        k = str(kind or "note").lower()
        k = k if k in FINDING_KINDS else "note"
        d = redact_text(str(detail or ""))[:1200]
        with self.db.lock:
            row = self.db.conn.execute("SELECT id,status FROM ai_findings WHERE title_key=?", (key,)).fetchone()
            if row is not None:
                if row["status"] == "REJECTED":
                    return {"recorded": False, "reason": "You rejected this earlier, so it is not suggested again."}
                self.db.conn.execute("UPDATE ai_findings SET detail=?,updated_at=? WHERE id=?", (d or "", _now(), row["id"]))
                self.db.conn.commit()
                return {"recorded": True, "id": int(row["id"]), "status": row["status"], "repeat": True}
            open_now = int(self.db.conn.execute("SELECT COUNT(*) FROM ai_findings WHERE status='OPEN'").fetchone()[0])
            if open_now >= MAX_OPEN_FINDINGS:
                return {"recorded": False, "reason": "The notebook is full of open findings: review some first."}
            cur = self.db.conn.execute("INSERT INTO ai_findings(kind,title,title_key,detail,status,created_at,updated_at) VALUES (?,?,?,?,'OPEN',?,?)", (k, t, key, d, _now(), _now()))
            self.db.conn.commit()
            return {"recorded": True, "id": int(cur.lastrowid), "status": "OPEN", "repeat": False}

    def list(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where, params = ("WHERE status=?", [status]) if status in STATUSES else ("", [])
        with self.db.lock:
            rows = self.db.conn.execute(f"SELECT id,kind,title,detail,status,created_at,updated_at FROM ai_findings {where} ORDER BY id DESC LIMIT ?", params + [max(1, min(200, int(limit)))]).fetchall()
        return [dict(r) for r in rows]

    def decide(self, finding_id: int, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError("Unknown status")
        with self.db.lock:
            cur = self.db.conn.execute("UPDATE ai_findings SET status=?,updated_at=? WHERE id=?", (status, _now(), int(finding_id)))
            self.db.conn.commit()
            return cur.rowcount > 0

    def counts(self) -> dict[str, int]:
        with self.db.lock:
            have = {r[0]: int(r[1]) for r in self.db.conn.execute("SELECT status,COUNT(*) FROM ai_findings GROUP BY status").fetchall()}
        return {s: have.get(s, 0) for s in STATUSES}
