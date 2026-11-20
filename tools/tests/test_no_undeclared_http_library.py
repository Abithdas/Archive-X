"""The installed app is built from requirements.txt, which does not list `requests`. Code that imports it directly works on a developer's machine
(it is there for other tools) and fails in the installed app. Outbound HTTP uses the standard library or core/http_client.py, which copes without it."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ALLOWED = {pathlib.Path("telegram_archive/core/http_client.py")}     # imports it inside a try and falls back to urllib


def test_requests_is_not_listed_so_nothing_else_may_import_it():
    listed = {re.split(r"[<>=\[;\s]", line.strip(), maxsplit=1)[0].lower() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    assert "requests" not in listed, "requests is now a declared dependency: this guard can go"
    offenders = []
    for path in (ROOT / "telegram_archive").rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel in ALLOWED:
            continue
        if re.search(r"^\s*(import requests|from requests\b|import urllib3|from urllib3\b)", path.read_text(encoding="utf-8"), re.M):
            offenders.append(str(rel))
    assert not offenders, f"imports a package the installed app does not have: {offenders}"
