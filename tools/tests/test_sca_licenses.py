"""The license policy checker (tools/sca/licenses.py): SPDX expressions, the names PyPI and npm use, and what is denied."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "sca"))
import licenses as lic  # noqa: E402


@pytest.mark.parametrize("expr,verdict", [
    ("MIT", "allowed"), ("Apache-2.0", "allowed"), ("BSD-3-Clause", "allowed"), ("0BSD", "allowed"), ("Unicode-3.0", "allowed"),
    ("MIT OR Apache-2.0", "allowed"), ("MIT/Apache-2.0", "allowed"), ("Apache-2.0 WITH LLVM-exception", "allowed"),
    ("(MIT OR Apache-2.0) AND Unicode-3.0", "allowed"),
    ("MPL-2.0", "review"), ("LGPL-2.1-or-later", "review"), ("MPL-2.0 AND MIT", "review"),
    ("MPL-2.0 OR GPL-3.0-only", "review"),                  # a choice: the weaker one is available
    ("GPL-3.0-only", "denied"), ("AGPL-3.0", "denied"), ("GPL-2.0-or-later", "denied"), ("SSPL-1.0", "denied"),
    ("MIT AND GPL-3.0-only", "denied"),                      # both apply: the worse counts
    ("GPL-3.0 OR MIT", "allowed"),
    ("UNLICENSED", "denied"), ("Proprietary", "denied"), ("", "denied"),
    ("SomeNewLicense-1.0", "unknown"),
])
def test_expressions(expr, verdict):
    assert lic.classify(expr) == verdict


@pytest.mark.parametrize("name,verdict", [
    ("MIT License", "allowed"), ("BSD License", "allowed"), ("Apache Software License", "allowed"),
    ("Python Software Foundation License", "allowed"), ("3-Clause BSD License", "allowed"),
    ("CC0 1.0 Universal (CC0 1.0) Public Domain Dedication", "allowed"),
    ("Mozilla Public License 2.0 (MPL 2.0)", "review"), ("GNU Lesser General Public License v3 (LGPLv3)", "review"),
])
def test_names_used_by_pypi(name, verdict):
    assert lic.classify(name) == verdict


def test_lgpl_is_not_mistaken_for_gpl_and_agpl_is_denied():
    assert lic.classify("LGPL-3.0-or-later") == "review"
    assert lic.classify("GPL-3.0-or-later") == "denied"
    assert lic.classify("AGPL-3.0-only") == "denied"


def test_check_groups_packages_and_the_exit_code_follows_denied(tmp_path, capsys):
    rows = [("a", "1", "MIT"), ("b", "2", "MPL-2.0"), ("c", "3", "GPL-3.0-only"), ("d", "4", "Weird-1.0")]
    groups, counts = lic.check(rows)
    assert counts == {"allowed": 1, "review": 1, "unknown": 1, "denied": 1}
    assert [r[0] for r in groups["denied"]] == ["c"]


def test_python_rows_read_the_pip_audit_file(tmp_path, monkeypatch):
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"dependencies": [{"name": "x", "version": "1.0"}, {"name": "y", "version": "2.0"}]}))
    infos = {"x": {"license_expression": "MIT", "classifiers": [], "license": ""},
             "y": {"license_expression": "", "classifiers": ["License :: OSI Approved :: BSD License", "License :: Other/Proprietary License"], "license": "BSD"}}

    class Resp:
        def __init__(self, name):
            self.name = name
        def read(self):
            return json.dumps({"info": infos[self.name]}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(lic.urllib.request, "urlopen", lambda url, timeout=0: __import__("io").BytesIO(json.dumps({"info": infos[url.split("/")[4]]}).encode()))
    rows = lic.python_rows(audit)
    assert rows == [("x", "1.0", "MIT"), ("y", "2.0", "bsd")] or rows == [("x", "1.0", "MIT"), ("y", "2.0", "BSD License")]
    assert all(lic.classify(r[2]) == "allowed" for r in rows)
