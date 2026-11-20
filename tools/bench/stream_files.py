"""Benchmark the file-serving stream paths through the real front door + backend on seeded data.

usage: bench.py <ax-server binary> [label]
Seeds: one SFW local folder with a 64 MB file, one NSFW local folder file, one SFW Telegram video with a ready
compat playback asset (64 MB). Measures seek latency (TTFB of 1 MB ranges at random offsets), sequential
throughput of the whole file, 4 parallel readers, and that SFW hides the NSFW file.
"""
import os, sys, json, subprocess, tempfile, time, threading, random, statistics
import requests
from concurrent.futures import ThreadPoolExecutor
ROOT = "/home/user/Archive-X-mobile-ready"
BIN = sys.argv[1]; LABEL = sys.argv[2] if len(sys.argv) > 2 else BIN
data = tempfile.mkdtemp(prefix="axbench_")
media = os.path.join(data, "folders"); os.makedirs(os.path.join(media, "sfw")); os.makedirs(os.path.join(media, "nsfw"))
SIZE = 64 * 1024 * 1024
blob = os.urandom(SIZE)
for p in (os.path.join(media, "sfw", "movie.mp4"), os.path.join(media, "nsfw", "clip.mp4")):
    open(p, "wb").write(blob)
SECRET = "s" * 40
env = {**os.environ, "ARCHIVE_X_DATA_DIR": data, "ARCHIVE_X_AUTO_OPEN_BROWSER": "0", "ARCHIVE_X_LOCAL_RESCAN": "0", "ARCHIVE_X_LAUNCH_SECRET": SECRET, "ARCHIVE_X_PROXY_KEY": "k" * 40, "ARCHIVE_X_DESKTOP": "1", "PYTHONUNBUFFERED": "1"}
seed = f'''
import sys, os
sys.path.insert(0, "{ROOT}")
from telegram_archive import config
from telegram_archive.core.database import Database
db = Database(config.DB_PATH)
c = db.conn
with db.lock:
    for sid, sub, rating in ((1, "sfw", "sfw"), (2, "nsfw", "nsfw")):
        root = os.path.join("{media}", sub)
        ch = str(-9_000_000_000_000 - sid)
        c.execute("INSERT INTO local_sources(id,path,created_at) VALUES(?,?,'x')", (sid, root))
        c.execute("INSERT INTO channels(telegram_id,title,username,role,in_library,content_rating,holds,updated_at) VALUES(?,?,?,'available',1,?,'both','x')", (ch, sub + " folder", "", rating))
        name = "movie.mp4" if sub == "sfw" else "clip.mp4"
        c.execute("INSERT INTO local_video_files(channel_id,message_id,path,size,mtime,probed,snapshots) VALUES(?,1,?,?,'x',1,36)", (ch, os.path.join(root, name), {SIZE}))
        c.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status,streamable,size) VALUES(?,1,?,?,'SCANNED',1,?)", (ch, name, name, {SIZE}))
    c.execute("INSERT INTO channels(telegram_id,title,username,role,in_library,content_rating,holds,updated_at) VALUES('-1002','Films','films','available',1,'sfw','both','x')")
    c.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status,streamable,size) VALUES('-1002',1,'film.mp4','film','SCANNED',1,{SIZE})")
    c.commit()
asset = config.PLAYBACK_ASSET_DIR / "-1002" / "1"
asset.mkdir(parents=True, exist_ok=True)
import shutil; shutil.copyfile(os.path.join("{media}", "sfw", "movie.mp4"), asset / "video.mp4")
db.close()
'''
subprocess.run([ROOT + "/.venv/bin/python", "-c", seed], env=env, check=True)
procs = []
def start(cmd):
    p = subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True); procs.append(p); return p
def ready(p, token="ARCHIVE_X_READY ", timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        line = p.stdout.readline()
        if token in line:
            threading.Thread(target=lambda: [None for _ in p.stdout], daemon=True).start()
            return json.loads(line.split(token, 1)[1].strip())
    raise SystemExit("not ready")
def pct(xs, q): xs = sorted(xs); return xs[min(len(xs) - 1, int(q * len(xs)))]
try:
    be = start([ROOT + "/.venv/bin/python", "-m", "telegram_archive.main"]); bport = ready(be)["port"]
    fd = start([BIN, "--listen", "127.0.0.1:0", "--upstream", f"127.0.0.1:{bport}", "--upstream-plain", "--print-ready", "--native-routes", "name_search,name_list,face_learning,all", "--dist", ROOT + "/apps/web/dist"]); fport = ready(fd)["port"]
    base = f"http://127.0.0.1:{fport}"
    s = requests.Session(); s.get(f"{base}/desktop/session", params={"token": SECRET}, timeout=20)
    cookies = s.cookies.get_dict()
    paths = {"local": "/stream/-9000000000001/1", "cached": "/stream-cached/-1002/1"}
    print(f"== {LABEL}")
    # correctness + SFW
    for k, p in paths.items():
        r = s.get(base + p, headers={"Range": "bytes=1000-1999"}, timeout=20)
        print(f"{k:7s} range check: {r.status_code} ok={r.content == blob[1000:2000]} cr={r.headers.get('content-range')} by={r.headers.get('x-served-by')}")
    r = s.get(base + "/stream/-9000000000002/1", headers={"Range": "bytes=0-9"}, timeout=20)
    print(f"nsfw file in SFW: {r.status_code} by={r.headers.get('x-served-by')}")
    L = "/stream/-9000000000001/1"; C = "/stream-cached/-1002/1"
    def chk(label, r, status, by=None, body=None):
        ok = r.status_code == status and (by is None or r.headers.get("x-served-by") == by) and (body is None or r.content == body)
        print(f"  {'PASS' if ok else 'FAIL'} {label}: {r.status_code} by={r.headers.get('x-served-by')} len={len(r.content)}")
    print("edge cases")
    chk("HEAD local", s.head(base + L), 200, "rust", b"")
    h = s.head(base + L); print("       head length", h.headers.get("content-length"), h.headers.get("content-type"), h.headers.get("accept-ranges"))
    chk("no Range = whole file", s.get(base + L), 200, "rust", blob)
    chk("suffix bytes=-10", s.get(base + L, headers={"Range": "bytes=-10"}), 206, "rust", blob[-10:])
    chk("open bytes=N-", s.get(base + L, headers={"Range": f"bytes={SIZE-5}-"}), 206, "rust", blob[-5:])
    chk("past end = 416", s.get(base + L, headers={"Range": f"bytes={SIZE}-"}), 416, "rust")
    chk("two ranges -> Python", s.get(base + L, headers={"Range": "bytes=0-1,5-6"}), 206 if False else s.get(base + L, headers={"Range": "bytes=0-1,5-6"}).status_code, "python")
    chk("If-Range -> Python", s.get(base + L, headers={"Range": "bytes=0-9", "If-Range": "x"}), 200, "python")
    chk("signed out refused (desktop guard)", requests.get(base + L, headers={"Range": "bytes=0-9"}), 403, "python")
    chk("unknown video -> Python 404", s.get(base + "/stream/-9000000000001/99"), 404, "python")
    chk("no cached copy -> Python 409", s.get(base + "/stream-cached/-1002/2"), 404 if False else s.get(base + "/stream-cached/-1002/2").status_code, "python")
    chk("telegram /stream stays Python", s.get(base + "/stream/-1002/1", headers={"Range": "bytes=0-9"}, timeout=30), s.get(base + "/stream/-1002/1", headers={"Range": "bytes=0-9"}, timeout=30).status_code, "python")
    import sqlite3
    dbp = os.path.join(data, "archive.db") if os.path.exists(os.path.join(data, "archive.db")) else None
    if dbp is None:
        for r_, _, fs in os.walk(data):
            for f in fs:
                if f.endswith(".db") and "archive" in f: dbp = os.path.join(r_, f)
    con = sqlite3.connect(dbp)
    # a file swapped for a link that leads out of its folder
    real = os.path.join(media, "sfw", "movie.mp4"); os.rename(real, real + ".bak"); os.symlink(os.path.join(media, "nsfw", "clip.mp4"), real)
    chk("link out of folder refused", s.get(base + L, headers={"Range": "bytes=0-9"}), 404)
    os.remove(real); os.rename(real + ".bak", real)
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('content_mode',?)", ('"nsfw"',)); con.commit()
    chk("NSFW mode: nsfw file served", s.get(base + "/stream/-9000000000002/1", headers={"Range": "bytes=0-9"}), 206, "rust", blob[:10])
    chk("NSFW mode: sfw file hidden", s.get(base + L, headers={"Range": "bytes=0-9"}), 404, "rust")
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('content_mode',?)", ('"sfw"',)); con.commit(); con.close()
    rnd = random.Random(7)
    for k, p in paths.items():
        for _ in range(5): s.get(base + p, headers={"Range": "bytes=0-1023"})  # warm
        lat = []
        for _ in range(200):
            off = rnd.randrange(0, SIZE - 1048576)
            t = time.perf_counter()
            with s.get(base + p, headers={"Range": f"bytes={off}-{off + 1048575}"}, stream=True) as r:
                next(r.iter_content(65536)); ttfb = time.perf_counter() - t
                for _ in r.iter_content(1 << 20): pass
            lat.append(ttfb * 1000)
        t = time.perf_counter(); n = 0
        with s.get(base + p, stream=True) as r:
            for c in r.iter_content(1 << 20): n += len(c)
        seq = n / (time.perf_counter() - t) / 1e6
        def reader(_):
            ss = requests.Session(); ss.cookies.update(cookies); m = 0
            with ss.get(base + p, stream=True) as r:
                for c in r.iter_content(1 << 20): m += len(c)
            return m
        t = time.perf_counter()
        with ThreadPoolExecutor(4) as ex: tot = sum(ex.map(reader, range(4)))
        par = tot / (time.perf_counter() - t) / 1e6
        print(f"{k:7s} seek TTFB ms p50={statistics.median(lat):.2f} p95={pct(lat, .95):.2f} p99={pct(lat, .99):.2f} | sequential {seq:.0f} MB/s | 4 readers {par:.0f} MB/s total")
finally:
    for p in procs: p.terminate()
    for p in procs:
        try: p.wait(10)
        except Exception: p.kill()
