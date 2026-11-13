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


# Routes whose answer is random, large, started from Telegram, or that start work: not comparable byte for byte.
SKIP_PARAM_ROUTES = ("/api/play/download", "/api/play/device-link", "/api/play/external", "/api/play/captions", "/api/play/range-cache",
                     "/api/play/buffer", "/api/play/audio-track", "/api/ai/evidence", "/telegram-photo", "/hls/", "/app-next/",
                     "/api/play/preview-frame", "/api/play/subtitle", "/api/play/preview/")


def discover(client, limit=3):
    """Real ids from the reference server, so routes with path parameters can be requested too."""
    def get_json(path):
        status, _, data = client.request("GET", path)
        try:
            return json.loads(data) if status == 200 else None
        except ValueError:
            return None
    seeds = {"channels": [], "media": [], "photos": [], "performers": [], "studios": [], "tags": []}
    channels = get_json("/api/channels") or []
    ids = [str(c.get("telegram_id")) for c in channels if c.get("telegram_id") is not None]
    seeds["channels"] = ids[:limit]
    lib = get_json("/api/library?channels=" + ",".join(ids) + "&page=0&per=%d&sort=date_desc" % limit) or {}
    seeds["media"] = [(str(i["source_channel"]), int(i["source_message_id"]), int(i["id"])) for i in lib.get("items", [])]
    photo_channels = get_json("/api/photos/channels") or {}
    for c in (photo_channels.get("channels") if isinstance(photo_channels, dict) else photo_channels) or []:
        if int(c.get("photo_count") or 0) > 0:
            ph = get_json("/api/photos?channels=%s&page=0&per=%d&q=&sort=date_desc" % (c["telegram_id"], limit)) or {}
            seeds["photos"] = [(str(i["channel_id"]), int(i["message_id"])) for i in ph.get("items", [])]
            if seeds["photos"]:
                break
    seeds["performers"] = [int(x["id"]) for x in (get_json("/api/performers?page=0&per=%d" % limit) or [])][:limit]
    seeds["studios"] = [int(x["id"]) for x in (get_json("/api/studios?page=0&per=%d" % limit) or [])][:limit]
    seeds["tags"] = [int(x["id"]) for x in (get_json("/api/tags") or []) if isinstance(x, dict) and "id" in x][:limit]
    return seeds


def expand(path, seeds):
    """Fill the {parameters} of a route from the discovered ids; [] when the route cannot be filled."""
    import re
    if any(k in path for k in SKIP_PARAM_ROUTES):
        return []
    names = re.findall(r"\{(\w+)\}", path)
    out = []
    if names == ["channel_id", "message_id"] or names == ["channel_id", "message_id", "index"] or names == ["channel_id", "message_id", "frame_index"]:
        pairs = seeds["photos"] if path.startswith("/api/photos/") else [(c, m) for c, m, _ in seeds["media"]]
        for c, m in pairs:
            out.append(path.replace("{channel_id}", c).replace("{message_id}", str(m)).replace("{index}", "0").replace("{frame_index}", "0"))
    elif names == ["channel_id"]:
        out = [path.replace("{channel_id}", c) for c in seeds["channels"]]
    elif names == ["media_id"]:
        out = [path.replace("{media_id}", str(m)) for _, _, m in seeds["media"]]
    elif names == ["performer_id"]:
        out = [path.replace("{performer_id}", str(i)) for i in seeds["performers"]]
    elif names == ["studio_id"]:
        out = [path.replace("{studio_id}", str(i)) for i in seeds["studios"]]
    elif names == ["tag_id"]:
        out = [path.replace("{tag_id}", str(i)) for i in seeds["tags"]]
    elif names == ["kind", "entity_id"]:
        out = [path.replace("{kind}", "performers").replace("{entity_id}", str(i)) for i in seeds["performers"]] + [path.replace("{kind}", "studios").replace("{entity_id}", str(i)) for i in seeds["studios"]]
    elif names == ["name"]:
        out = [path.replace("{name}", "inter-400.woff2")]
    return out


def routes_with_params():
    spec = json.loads((ROOT / "docs/desktop/api/openapi.json").read_text())
    return [p for p, ops in spec["paths"].items() if "get" in ops and "{" in p]


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
    ap.add_argument("--discover", action="store_true", help="also request routes with path parameters, filled with ids found on server A")
    ap.add_argument("--queries", action="store_true", help="also request the list-route query variants in tools/parity/queries.txt")
    ap.add_argument("--ignore", default="", help="extra JSON keys to ignore, comma separated")
    ap.add_argument("--skip", default="/api/exit,/openapi.json,/api/diagnostics/metrics,/api/diagnostics/metrics/export,/api/diagnostics/report,/api/diagnostics/summary", help="paths not to request")
    args = ap.parse_args()
    ignore = DEFAULT_IGNORE | {k for k in args.ignore.split(",") if k}
    skip = set(args.skip.split(","))
    paths = [p for p in routes_from_openapi() if p not in skip]
    if args.routes:
        paths += [l.strip() for l in Path(args.routes).read_text().splitlines() if l.strip() and not l.startswith("#")]
    a, b = Client(args.a, args.user, args.password), Client(args.b, args.user, args.password)
    if args.discover:
        seeds = discover(a)
        print("discovered:", {k: (len(v) if k != "channels" else v) for k, v in seeds.items()})
        for tpl in routes_with_params():
            paths += expand(tpl, seeds)
    if args.queries:
        qpath = Path(__file__).with_name("queries.txt")
        extra = [l.strip() for l in qpath.read_text().splitlines() if l.strip() and not l.startswith("#")]
        if args.discover:
            ids = ",".join(seeds["channels"]) or ""
            extra = [q.replace("{channels}", ids) for q in extra]
        paths += extra
    bad = same = 0
    statuses = {}
    for p in paths:
        try:
            sa, ha, da = a.request("GET", p)
            sb, hb, db_ = b.request("GET", p)
            statuses[sa] = statuses.get(sa, 0) + 1
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
    print(f"\nanswers by status (server A): {dict(sorted(statuses.items()))}")
    print(f"{same} identical, {bad} different, {len(paths)} routes checked")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
