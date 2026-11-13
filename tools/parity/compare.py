"""Send the same requests to two servers (the Python backend and the Rust one) and report every difference.

    python tools/parity/compare.py --a https://127.0.0.1:8443 --b https://127.0.0.1:9443 --user admin --password ...

By default it tests every GET route of docs/desktop/api/openapi.json that has no path parameter. Extra routes
(one per line, e.g. /api/library?page=0&per=3) can be added with --routes FILE. Status codes and JSON bodies must
match; keys named in --ignore (volatile values such as timestamps) are removed before comparing.
Exit code 0 only when nothing differs.
"""
import argparse
import http.client
import json
import ssl
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IGNORE = {"updated_at", "created_at", "now", "time", "timestamp", "uptime", "uptime_seconds", "elapsed", "elapsed_seconds",
                  "last_seen_at", "generated_at", "started_at", "ts", "request_id", "trace_id", "age_seconds", "heartbeat_at"}


class Client:
    def __init__(self, base, user, password):
        u = urllib.parse.urlparse(base)
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80)
        self.base, self.cookie = base, ""
        self.ctx = ssl._create_unverified_context()
        if user:
            body = json.dumps({"username": user, "password": password})
            status, headers, _ = self.request("POST", "/login", body, {"Content-Type": "application/json", "Origin": base})
            self.cookie = "; ".join(v.split(";")[0] for k, v in headers if k.lower() == "set-cookie")
            if not self.cookie:
                sys.exit(f"login to {base} gave no session cookie (status {status})")

    def request(self, method, path, body=None, headers=None, timeout=60):
        conn = (http.client.HTTPSConnection(self.host, self.port, context=self.ctx, timeout=timeout) if self.scheme == "https"
                else http.client.HTTPConnection(self.host, self.port, timeout=timeout))
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        out = (r.status, r.getheaders(), data)
        conn.close()
        return out


def scrub(v, ignore):
    if isinstance(v, dict):
        return {k: scrub(x, ignore) for k, x in v.items() if k not in ignore and not k.endswith(('_age', '_age_seconds', '_ms', '_at'))}
    if isinstance(v, list):
        return [scrub(x, ignore) for x in v]
    return v


def diff(a, b, path=""):
    if type(a) is not type(b):
        return [f"{path or '/'}: {type(a).__name__} vs {type(b).__name__}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}/{k}: only in B")
            elif k not in b:
                out.append(f"{path}/{k}: only in A")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path or '/'}: list length {len(a)} vs {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out[:5]
    return [] if a == b else [f"{path or '/'}: {str(a)[:60]!r} vs {str(b)[:60]!r}"]


def routes_from_openapi():
    spec = json.loads((ROOT / "docs/desktop/api/openapi.json").read_text())
    return [p for p, ops in spec["paths"].items() if "get" in ops and "{" not in p]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="reference server (Python)")
    ap.add_argument("--b", required=True, help="server under test (Rust)")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--routes", help="file with extra GET paths, one per line")
    ap.add_argument("--ignore", default="", help="extra JSON keys to ignore, comma separated")
    ap.add_argument("--skip", default="/api/exit,/openapi.json,/api/diagnostics/metrics,/api/diagnostics/metrics/export,/api/diagnostics/report,/api/diagnostics/summary", help="paths not to request")
    args = ap.parse_args()
    ignore = DEFAULT_IGNORE | {k for k in args.ignore.split(",") if k}
    skip = set(args.skip.split(","))
    paths = [p for p in routes_from_openapi() if p not in skip]
    if args.routes:
        paths += [l.strip() for l in Path(args.routes).read_text().splitlines() if l.strip() and not l.startswith("#")]
    a, b = Client(args.a, args.user, args.password), Client(args.b, args.user, args.password)
    bad = same = 0
    for p in paths:
        try:
            sa, ha, da = a.request("GET", p)
            sb, hb, db_ = b.request("GET", p)
        except Exception as e:
            print(f"ERROR {p}: {type(e).__name__}: {e}")
            bad += 1
            continue
        problems = []
        if sa != sb:
            problems.append(f"status {sa} vs {sb}")
        else:
            ta = dict((k.lower(), v) for k, v in ha).get("content-type", "").split(";")[0]
            tb = dict((k.lower(), v) for k, v in hb).get("content-type", "").split(";")[0]
            if ta != tb:
                problems.append(f"content-type {ta!r} vs {tb!r}")
            elif ta == "application/json":
                try:
                    problems += diff(scrub(json.loads(da), ignore), scrub(json.loads(db_), ignore))[:3]
                except ValueError:
                    problems.append("invalid JSON in one answer")
            elif da != db_ and not ta.startswith(("text/event-stream",)):
                problems.append(f"body differs ({len(da)} vs {len(db_)} bytes)")
        if problems:
            bad += 1
            print(f"DIFF  {p}\n      " + "\n      ".join(problems))
        else:
            same += 1
    print(f"\n{same} identical, {bad} different, {len(paths)} routes checked")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
