"""Record what Python's `ai/ai_safety.py` does, for `crates/ax-ai/tests/safety.rs`.

    python tools/parity/gen_ai_safety_vectors.py          # rewrite crates/ax-ai/tests/safety_cases.json
    python tools/parity/gen_ai_safety_vectors.py --check  # fail if the file is out of date

Three recordings: `redact_text` over a few thousand strings (every pattern, near misses, odd letter cases and Unicode digits, white space and letters
Python's `re` treats in its own way), `is_secret_name` over field names, `redact` over nested values, and scripted conversations with the
`PermissionBroker` (a fake clock, numbered ids, a few executors that succeed or fail) with the whole state and the log lines after every step.

The strings are made of fake secrets, so the file keeps every text as short pieces that are joined when it is read: no piece looks like a key, a token
or a private key to a scanner, and the file can be committed.
"""
import json
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-ai" / "tests" / "safety_cases.json"

from telegram_archive.ai import ai_safety as S  # noqa: E402

PIECE = 5


def pieces(text: str) -> list:
    return [text[i:i + PIECE] for i in range(0, len(text), PIECE)] or [""]


# ------------------------------------------------------------------ the strings
def fragments() -> list:
    jwt = "ey" + "J" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    bot = "123456" + ":" + "ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END " + "RSA PRIVATE KEY-----"
    mixed40 = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9aB1cD3eF5gH7j"
    return [
        "bearer ", "Bearer ", "BASIC ", "basic  ", "token", "Token", "TOKEN", "api_key", "api-key", "api key", "apikey", "API_KEY", "api_hash", "api-hash", "password", "passwd",
        "passphrase", "pass", "secret", "client_secret", "authorization", "Authorization", "cookie", "session", "session_id", "session-string", "session_string", "private_key",
        "private-key", "=", ":", ": ", " = ", "=\"", ": '", "'", "\"", ",", ";", "}", "]", " ", "  ", "\t", "\n", "\r\n", "\x1c", "\x1f", "\u00a0", "\u2003", "\u3000",
        "abcdefgh", "abc12345678", "Zm9vYmFy+/=", "a.b~c", "x" * 7, "y" * 8, "12345", "123456", "1234567", mixed40, mixed40[:39], mixed40 + "z", "A" * 45, "a" * 45, "a1" * 25,
        "deadbeef" * 4, "deadbeef" * 4 + "0", "DEADBEEF" * 5, "0123456789abcdef" * 2, "0123456789abcdeg" * 2, bot, bot[:-1], "12345" + ":" + "A" * 30, jwt, jwt.replace(".", "-"),
        "eyJ" + "abcdefgh.abcdefgh.abcdefg", "http" + "s://user:pw@host/x", "http" + "s://us%40er:p%3Aw@host", "//a:b@c", "//a:b", "ftp://:@h", pem, pem.replace("RSA ", ""),
        "-----BEGIN PRIVATE KEY-----\nxx\n-----END PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----\nunfinished",
        "\u0130", "\u0131", "\u017f", "\u212a", "\u00df", "\u00e9", "日本", "٣٤٥٦٧٨", "１２３４５６７", "_", "-", ".", "/", "~", "+", "@", "a", "Z", "0", "9",
        "to\u212aen", "pa\u017f\u017fword", "\u0130d", "se\u017f\u017fion", "coo\u212aie", "BEARER\u00a0abcdefgh", "token:", "token =", "token\t=\tx", "my-token-value", "tokens", "mytoken=1",
    ]


def texts() -> list:
    rnd = random.Random(20261121)
    frag = fragments()
    out = []
    # each fragment alone and in front of a value
    for f in frag:
        out += [f, f + " abcdefgh12", "x " + f + " y", f + "=" + "v" * 12, f + ": \"" + "k" * 10 + "\""]
    # words with their i, s and k replaced by the letters re.IGNORECASE ties to them
    swap = {"i": ["\u0130", "\u0131", "I"], "s": ["\u017f", "S"], "k": ["\u212a", "K"]}
    for word in ["bearer", "basic", "token", "secret", "password", "passwd", "passphrase", "authorization", "cookie", "session", "api_key", "api_hash", "private_key", "string_session"]:
        for _ in range(6):
            w = "".join(rnd.choice(swap[c]) if c in swap and rnd.randrange(2) else c for c in word)
            out += [w + "=" + "v" * 12, w + " " + "w" * 12]
    # random mixtures
    for _ in range(1100):
        out.append("".join(rnd.choice(frag) for _ in range(rnd.randrange(1, 9))))
    # near misses at the length thresholds
    for n in (5, 6, 7, 8, 9, 29, 30, 31, 32, 33, 39, 40, 41):
        out += ["bearer " + "a" * n, "a" * n, "1" * n, "123456:" + "A" * n, "ey" + "J" + "a" * n + "." + "a" * n + "." + "a" * n, "f" * n, "aB3" * (n // 3 + 1)]
    out += ["", " ", "\n", "plain words, nothing to hide", "scene_name_with_underscores_and_a_very_long_name_that_goes_on_and_on_and_on_past_forty_chars.mp4",
            "C:\\Users\\me\\Videos\\" + "A1b" * 20, "x" * 3000, ("token=" + "a" * 20 + " ") * 40]
    return out


def names() -> list:
    base = ["token", "api_key", "apikey", "api-key", "api key", "API_KEY", "api_hash", "apihash", "api_id", "apiid", "my_api_id", "myapiid", "password", "pass", "passwd", "passphrase", "bypass",
            "compass", "password_hash", "pin", "spin", "pin_code", "otp", "otp_code", "salt", "asalt", "salty", "2fa", "x2fa", "totp2fa", "cookie", "cookie_jar", "Session", "session_id",
            "sessions_total", "token_count", "max_tokens", "tokens", "total_sessions", "num_sessions", "session_count", "secret", "client_secret", "secret_size", "secret_limit", "authorization",
            "authorisation", "authorizationHeader", "credential", "credentials", "private_key", "privatekey", "private key", "signing_key", "signing secret", "signing-key", "signingsecret",
            "string_session", "stringsession", "string session", "bearer", "bearer_token", "web_username", "title", "name", "duration", "size", "rate", "bit_rate", "rate_limit", "per_second",
            "used", "ms", "seconds", "timeout_seconds", "tokens_used", "max_pass", "min_token", "num_token", "total_cookie", "count", "api", "key", "keys", "monkey", "hash", "id", "", " ",
            "token_count\n", "rate\n", "token\n", "token_count\n\n", "TOKEN_COUNT", "Token_Total", "token_ms", "token_number", "token_numbers", "token_rates", "token_RATE"]
    swap = {"i": ["\u0130", "\u0131"], "s": ["\u017f"], "k": ["\u212a"]}
    rnd = random.Random(7)
    out = list(base)
    for b in base:
        for _ in range(2):
            out.append("".join(rnd.choice(swap[c]) if c in swap and rnd.randrange(2) else c for c in b))
    parts = ["token", "key", "pass", "_", "-", " ", "api", "id", "session", "count", "max", "x", "A", "1", "\x1c", "\n", "secret", "salt", "pin", "otp", "\u00e9"]
    for _ in range(400):
        out.append("".join(rnd.choice(parts) for _ in range(rnd.randrange(1, 6))))
    return out


def values() -> list:
    rnd = random.Random(11)
    secret = "v" * 12
    out = [None, True, False, 0, 1, 2.5, -0.0, "", "hi", [], {}, [1, "a", None], (1, 2) and [1, 2],
           {"api_key": secret, "title": "x"}, {"api_key": "", "x": 1}, {"api_key": 0}, {"api_key": 0.0}, {"api_key": False}, {"api_key": None}, {"api_key": []}, {"api_key": {}},
           {"api_key": True}, {"api_key": 1}, {"api_key": [1]}, {"api_key": {"a": 1}}, {"token_count": 5, "token": "t" * 8}, {"session": {"id": 1, "cookie": secret}},
           {"a": [{"password": secret}, {"b": "bearer " + secret}]}, {"note": "api_key=" + secret}, {"list": ["token: " + secret, 3, None]}]
    deep = "end token=" + secret
    for depth in (10, 11, 12, 13, 14):
        v = deep
        for _ in range(depth):
            v = {"k": v}
        out.append(v)
        w = deep
        for _ in range(depth):
            w = [w]
        out.append(w)
    keys = ["token", "name", "api_key", "x", "password", "count", "session_cookie", "title"]
    vals = [None, "", 0, 1, False, True, "text", "bearer " + secret, 0.0, 3.5, [], {}, "pass=" + secret]

    def gen(d):
        if d <= 0:
            return rnd.choice(vals)
        r = rnd.randrange(3)
        if r == 0:
            return {rnd.choice(keys): gen(d - 1) for _ in range(rnd.randrange(1, 4))}
        if r == 1:
            return [gen(d - 1) for _ in range(rnd.randrange(0, 4))]
        return rnd.choice(vals)

    out += [gen(rnd.randrange(1, 5)) for _ in range(150)]
    return out


# ------------------------------------------------------------------ the broker
EXECUTORS = {
    "ok": lambda args: {"count": 3, "api_key": "k" * 12, "note": "bearer " + "x" * 12, "args": args},
    "echo": lambda args: dict(args),
    "fail_value": lambda args: (_ for _ in ()).throw(ValueError("boom")),
    "fail_secret": lambda args: (_ for _ in ()).throw(RuntimeError("token=" + "q" * 10 + " failed")),
    "fail_long": lambda args: (_ for _ in ()).throw(KeyError("z" * 500)),
}

LONG = "word " * 100


def scenarios() -> list:
    reg = lambda a, e="ok": {"op": "register", "action": a, "executor": e}  # noqa: E731
    prop = lambda a, summary="do it", items=None, args=None: {"op": "propose", "action": a, "summary": summary, "items": items or [], "args": args or {}}  # noqa: E731
    dec = lambda i, approve=True, typed="": {"op": "decide", "n": i, "approve": approve, "typed": typed}  # noqa: E731
    run = lambda i: {"op": "run", "n": i}  # noqa: E731
    adv = lambda s: {"op": "advance", "seconds": s}  # noqa: E731
    return [
        {"name": "a reversible action from proposal to result", "ops": [reg("start_scan"), prop("start_scan", "scan the library", args={"channel": 5}), {"op": "pending"}, dec(0), run(0), run(0), dec(0), {"op": "pending"}]},
        {"name": "refusals", "ops": [prop("read_secret"), prop("run_shell"), prop("run_sql"), prop("fetch_url"), prop("nothing_like_it"), prop(""), reg("run_shell"), reg("unknown"), reg("fetch_url"), {"op": "pending"}]},
        {"name": "protected settings", "ops": [reg("change_setting", "echo")] + [prop("change_setting", "set it", args={"name": n, "value": 1}) for n in
                                           ["theme", "api_key", "web_username", "WEB_USERNAME", "my_Token", "session_timeout", "endpoint_url", "ui_pass", "bypass_x", "tls_mode", "cert_path", "autoplay", "key", "k\u212aey", "se\u017fsion", "x"]]
         + [prop("change_setting", "no name"), prop("change_setting", "none", args={"name": None}), prop("change_setting", "number", args={"name": 5}), {"op": "pending"}]},
        {"name": "destructive actions", "ops": [reg("delete_media"), reg("apply_code_patch"), reg("send_to_telegram"), reg("bulk_edit"), reg("move_media"),
                                                prop("delete_media", "no items"), prop("delete_media", "one", items=["a"]), dec(0), prop("delete_media", "three", items=["a", "b", "c"]), dec(1, True), dec(1, True, "delete 2"),
                                                dec(1, True, "  delete 3  "), run(1), prop("apply_code_patch", "p", items=["x", "y"]), dec(2, True, "\x1cAPPLY 2\x1f"), prop("send_to_telegram", "s", items=["1", "2", "3", "4"]),
                                                dec(3, True, "SEND 4"), prop("bulk_edit", "b", items=["i"] * 5), dec(4, True, "bulk 5"), prop("move_media", "m", items=["\u00e9"] * 2), dec(5, True, "MOVE 2"),
                                                prop("delete_performer", "dp", items=["a", "b"]), dec(6, True, "d\u00e9lete 2")]},
        {"name": "long texts and many items", "ops": [reg("delete_media"), prop("delete_media", LONG, items=[f"item {i} " + "x" * 300 for i in range(60)]), {"op": "pending"}, dec(0, True, "DELETE 60"), run(0),
                                                    prop("start_scan", "token=" + "s" * 20 + " " + LONG), {"op": "pending"}]},
        {"name": "proposals expire", "ops": [reg("start_scan"), prop("start_scan", "one"), adv(300), prop("start_scan", "two"), adv(299.5), {"op": "pending"}, adv(0.5), {"op": "pending"}, {"op": "get", "n": 0}, dec(0), dec(1), {"op": "get", "n": 1},
                                             prop("start_scan", "three"), dec(2), adv(599), run(2), adv(1), run(2), {"op": "get", "n": 2}, adv(30), {"op": "get", "n": 2}, {"op": "get", "n": 0}]},
        {"name": "declining and running early", "ops": [reg("queue_job"), prop("queue_job", "q"), run(0), dec(0, False), dec(0, True), run(0), {"op": "decide_unknown"}, {"op": "run_unknown"}, prop("start_scan", "no executor"), dec(1), run(1)]},
        {"name": "executors that fail", "ops": [reg("start_scan", "fail_value"), reg("queue_job", "fail_secret"), reg("rerun_metadata_match", "fail_long"), reg("apply_suggestion", "echo"),
                                                prop("start_scan", "a"), dec(0), run(0), prop("queue_job", "b"), dec(1), run(1), prop("rerun_metadata_match", "c"), dec(2), run(2),
                                                prop("apply_suggestion", "d", args={"password": "p" * 10, "text": "bearer " + "t" * 12, "n": 3}), dec(3), run(3), run(0), dec(0)]},
        {"name": "several proposals at once", "ops": [reg("start_scan"), reg("queue_job"), prop("start_scan", "1"), prop("queue_job", "2"), prop("start_scan", "3"), dec(1), {"op": "pending"}, dec(0, False), {"op": "pending"}, adv(700), {"op": "pending"}]},
        {"name": "re-registering an executor", "ops": [reg("start_scan", "fail_value"), reg("start_scan", "ok"), prop("start_scan", "s"), dec(0), run(0)]},
    ]


def run_scenario(sc: dict) -> dict:
    now = [1_900_000_000.0]
    counter = [0]
    S.time = types.SimpleNamespace(time=lambda: now[0])
    S.secrets = types.SimpleNamespace(token_hex=lambda n: f"id{counter.__setitem__(0, counter[0] + 1) or counter[0]:04d}")
    log = []
    broker = S.PermissionBroker(log=lambda msg, level="INFO", **kw: log.append([msg, level, kw.get("event"), kw.get("details")]), clock=lambda: now[0])
    made = []
    steps = []

    def state():
        return [[p.id, p.action, p.status, p.needs_typed, p.created, p.expires, p.summary, p.items, p.args, p.result, [list(h) for h in p.history], p.public()] for p in made]

    for op in sc["ops"]:
        kind = op["op"]
        try:
            if kind == "register":
                broker.register(op["action"], EXECUTORS[op["executor"]])
                res = "registered"
            elif kind == "propose":
                p = broker.propose(op["action"], op["summary"], items=list(op["items"]), args=dict(op["args"]))
                made.append(p)
                res = p.public()
            elif kind == "decide":
                res = broker.decide(made[op["n"]].id, op["approve"], typed=op["typed"]).public()
            elif kind == "run":
                res = broker.run(made[op["n"]].id).public()
            elif kind == "get":
                p = broker.get(made[op["n"]].id)
                res = p.public() if p else None
            elif kind == "pending":
                res = [p.id for p in broker.pending()]
            elif kind == "decide_unknown":
                broker.decide("nope", True)
            elif kind == "run_unknown":
                broker.run("nope")
            elif kind == "advance":
                now[0] += op["seconds"]
                res = "advanced"
            else:
                raise SystemExit(kind)
            steps.append({"ok": res})
        except S.NotAllowed as exc:
            steps.append({"error": str(exc)})
    return {**sc, "expect": {"steps": steps, "state": state(), "log": log}}


def render() -> str:
    data = {
        "texts": [[pieces(t), pieces(S.redact_text(t))] for t in texts()],
        "names": [[pieces(n), S.is_secret_name(n)] for n in names()],
        "values": [[json.loads(json.dumps(v)), S.redact(v)] for v in values()],
        "broker": [run_scenario(sc) for sc in scenarios()],
    }
    # the values hold secret-looking strings too: store them as pieces
    def chunk(v):
        if isinstance(v, str):
            return {"p": pieces(v)}
        if isinstance(v, dict):
            return {"d": [[pieces(k), chunk(x)] for k, x in v.items()]}
        if isinstance(v, list):
            return {"l": [chunk(x) for x in v]}
        return {"v": v}
    data["values"] = [[chunk(a), chunk(b)] for a, b in data["values"]]
    return "{\n" + ",\n".join(f'"{k}": ' + ("[\n" + ",\n".join(json.dumps(x, ensure_ascii=False) for x in v) + "\n]") for k, v in data.items()) + "\n}\n"


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is out of date; run python tools/parity/gen_ai_safety_vectors.py")
            sys.exit(1)
        print("ai safety vectors are up to date")
        return
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(text) // 1024} KiB")


if __name__ == "__main__":
    main()
