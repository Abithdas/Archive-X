"""License policy for the dependencies Archive X is built from.

    python tools/sca/licenses.py rust <folder with Cargo.toml>      (needs cargo)
    python tools/sca/licenses.py npm  <folder with package-lock.json>  (after npm ci; needs npx)
    python tools/sca/licenses.py python <pip-audit json>            (pip-audit -r requirements.txt -f json -o file)
    add --report FILE to also write the table as Markdown

The rule (the app is shipped to other people, so what it contains matters):
  allowed   permissive licenses (MIT, Apache-2.0, BSD, ISC, Zlib, Unicode, 0BSD, CC0, Unlicense, PSF, ...)
  review    weak copyleft that is fine when used as a library but should be a conscious choice (MPL-2.0, LGPL, EPL, CDDL): listed, not failed
  denied    GPL, AGPL, SSPL, EUPL, anything proprietary or without a license: fails the check
An expression like "MIT OR Apache-2.0" passes when any alternative is allowed; "A AND B" needs both. Exit code 1 when anything is denied.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ALLOWED = {
    "mit", "mit-0", "mit-cmu", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "bsd", "isc", "zlib", "0bsd", "cc0-1.0", "unlicense",
    "psf-2.0", "python-2.0", "unicode-3.0", "unicode-dfs-2016", "bsl-1.0", "ncsa", "blueoak-1.0.0", "cdla-permissive-2.0", "openssl",
    "libpng-2.0", "curl", "hpnd", "wtfpl", "public domain", "mpl-2.0-no-copyleft-exception",
}
REVIEW = {"mpl-2.0", "lgpl-2.1", "lgpl-2.1-only", "lgpl-2.1-or-later", "lgpl-3.0", "lgpl-3.0-only", "lgpl-3.0-or-later", "epl-1.0", "epl-2.0", "cddl-1.0", "cddl-1.1"}
DENIED_PATTERN = re.compile(r"(^|[^l])gpl|agpl|sspl|eupl|proprietary|commercial|unlicensed|cc-by-nc|^$", re.I)

# Names that PyPI classifiers and npm use, mapped to SPDX ids.
ALIASES = {
    "mit license": "mit", "bsd license": "bsd", "apache software license": "apache-2.0", "apache 2.0": "apache-2.0", "apache license 2.0": "apache-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "mpl-2.0", "python software foundation license": "psf-2.0", "3-clause bsd license": "bsd-3-clause",
    "cc0 1.0 universal (cc0 1.0) public domain dedication": "cc0-1.0", "the unlicense (unlicense)": "unlicense", "isc license (iscl)": "isc",
    "zlib/libpng license": "zlib", "gnu lesser general public license v3 (lgplv3)": "lgpl-3.0",
}


def classify_id(name: str) -> str:
    raw = name.strip().lower()
    n = ALIASES.get(raw) or ALIASES.get(raw.strip("*()")) or raw.strip("*()")
    if n in ALLOWED:
        return "allowed"
    if n in REVIEW:
        return "review"
    if DENIED_PATTERN.search(n):
        return "denied"
    return "unknown"


_TOKEN = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+", re.I)
_RANK = {"allowed": 0, "review": 1, "unknown": 2, "denied": 3}


def classify(expr: str) -> str:
    """allowed / review / unknown / denied for an SPDX-style expression (also accepts the old `A/B` and `A; B` spellings)."""
    whole = (expr or "").strip().lower()
    if whole in ALIASES:   # a full name such as "Apache Software License" is one license, not three words
        return classify_id(whole)
    expr = (expr or "").replace("/", " OR ").replace(";", " OR ")
    tokens = _TOKEN.findall(expr)
    pos = 0

    def atom() -> str:
        nonlocal pos
        if pos >= len(tokens):
            return "unknown"
        t = tokens[pos]
        if t == "(":
            pos += 1
            r = disjunction()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return r
        pos += 1
        if pos < len(tokens) and tokens[pos].upper() == "WITH":   # "Apache-2.0 WITH LLVM-exception": the exception only widens the permission
            pos += 2
        return classify_id(t)

    def conjunction() -> str:
        nonlocal pos
        r = atom()
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            r = max(r, atom(), key=_RANK.get)   # all of them apply: the worst one counts
        return r

    def disjunction() -> str:
        nonlocal pos
        r = conjunction()
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            r = min(r, conjunction(), key=_RANK.get)   # a choice: the best one counts
        return r

    return disjunction() if tokens else "denied"


def rust_rows(folder: Path) -> list[tuple[str, str, str]]:
    out = subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked"], cwd=folder, capture_output=True, text=True, check=True).stdout
    meta = json.loads(out)
    workspace = set(meta["workspace_members"])
    return sorted({(p["name"], p["version"], p.get("license") or p.get("license_file") and "LicenseRef-file" or "") for p in meta["packages"] if p["id"] not in workspace})


def npm_rows(folder: Path) -> list[tuple[str, str, str]]:
    out = subprocess.run(["npx", "--yes", "license-checker-rseidelsohn", "--production", "--json"], cwd=folder, capture_output=True, text=True, check=True).stdout
    rows = []
    for key, v in json.loads(out).items():
        name, _, version = key.rpartition("@")
        lic = v.get("licenses")
        rows.append((name, version, " OR ".join(lic) if isinstance(lic, list) else str(lic)))
    return [r for r in sorted(rows) if r[0] != "archive-x-frontend"]   # the project itself is private ("UNLICENSED")


def python_rows(audit_json: Path) -> list[tuple[str, str, str]]:
    rows = []
    for dep in json.loads(audit_json.read_text())["dependencies"]:
        name, version = dep["name"], dep["version"]
        try:
            info = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30))["info"]
        except Exception as exc:
            rows.append((name, version, f"lookup failed: {exc.__class__.__name__}"))
            continue
        expr = (info.get("license_expression") or "").strip()
        classes = [c.split("::")[-1].strip() for c in info.get("classifiers", []) if c.startswith("License ::")]
        field = (info.get("license") or "").strip().splitlines()[0][:60] if info.get("license") else ""
        known = [ALIASES.get(c.lower(), c) for c in classes if "Other/Proprietary" not in c]   # PyPI classifiers, as SPDX ids where we know them
        rows.append((name, version, expr or " OR ".join(known) or field))
    return sorted(rows)


def check(rows: list[tuple[str, str, str]]) -> tuple[dict[str, list], dict[str, int]]:
    groups: dict[str, list] = {"allowed": [], "review": [], "unknown": [], "denied": []}
    for name, version, lic in rows:
        groups[classify(lic)].append((name, version, lic))
    return groups, {k: len(v) for k, v in groups.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["rust", "npm", "python"])
    ap.add_argument("path")
    ap.add_argument("--report")
    a = ap.parse_args()
    rows = {"rust": rust_rows, "npm": npm_rows, "python": python_rows}[a.kind](Path(a.path))
    groups, counts = check(rows)
    print(f"{a.kind}: {len(rows)} packages  " + "  ".join(f"{k} {v}" for k, v in counts.items()))
    for kind in ("review", "unknown", "denied"):
        for name, version, lic in groups[kind]:
            print(f"  {kind.upper():8} {name} {version}: {lic or '(none)'}")
    if a.report:
        lines = [f"### {a.kind}: {len(rows)} packages", "", "| package | version | license | verdict |", "|---|---|---|---|"]
        for kind in ("denied", "unknown", "review", "allowed"):
            lines += [f"| {n} | {v} | {l or '(none)'} | {kind} |" for n, v, l in groups[kind]]
        Path(a.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if groups["denied"] else 0


if __name__ == "__main__":
    sys.exit(main())
