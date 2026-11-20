"""Build a small, deterministic, populated Archive X database for the parity checks.

    python tools/parity/seed_fixture.py --data-dir /path/to/data

Creates <data-dir>/database/archive.db (the normal schema, through the real Database class) and the photo cache files:
4 channels, 72 videos with performers, studios and tags, 32 photos, 3 face groups, and performer face vectors.
Every date is fixed and every random choice is seeded, so two data folders built by this script are identical and a
difference between the Python and the Rust answers is a real difference.

It does not create a login: sign in once on each server being compared (the first-run setup form, or
POST /api/auth/setup) with the same user name and password, and give them to tools/parity/compare.py.
Order matters: the server re-creates the library views each time it starts, which hides the videos from the library list until
the seed's own views replace them. So start the server first, then run this script (it can be run again at any time; it resets
the videos and photos to the fixture state). The database is created if it does not exist yet.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE = datetime.datetime(2025, 6, 1, 12, 0, 0)


def iso(days: int) -> str:
    return (BASE - datetime.timedelta(days=days)).isoformat()


def seed_videos(c):
    random.seed(7)
    c.execute("DROP VIEW IF EXISTS archive_x_library_media")
    c.execute("DROP VIEW IF EXISTS archive_x_published_media")
    c.execute("CREATE VIEW archive_x_library_media AS SELECT m.*, 1 AS library_ready FROM media m")
    c.execute("CREATE VIEW archive_x_published_media AS SELECT m.* FROM media m")
    chs = [("-1001", "Main Archive", "main"), ("-1002", "Vertical Clips", "vertical"), ("-1003", "A Very Long Channel Name That Should Truncate Nicely", "main"), ("-1004", "Misc", "main")]
    for i, (tid, t, role) in enumerate(chs, 1):
        c.execute("INSERT OR REPLACE INTO channels(id,telegram_id,title,username,role,updated_at) VALUES(?,?,?,?,?,?)", (i, tid, t, "ch%d" % i, role, iso(0)))
    c.execute("INSERT OR REPLACE INTO channels(id,telegram_id,title,username,role,updated_at) VALUES(5,'-1009999','Not yet scanned channel','ch5','main',?)", (iso(0),))
    perf = ["Ava Stone", "Mia Rivera", "Zoë Castellanos-Whitfield", "Li", "Priya Raman", "Jordan Lee", "Sofia Marchetti", "Nina Volkov", "Amélie Dubois", "Chloe Park", "Isabella Fernández de la Cruz", "Ruby"]
    for i, n in enumerate(perf, 1):
        c.execute("INSERT OR REPLACE INTO performers(id,name,normalized_name,gender,image_crop_version,profile,bio) VALUES(?,?,?,?,0,?,?)",
                  (i, n, n.lower(), "female" if i % 4 else "male", "Born 1995 · 168 cm", "Short bio text. " * (i % 5 + 1)))
    studs = ["Northlight", "Aurora Films", "Big Studio Productions International", "Zed", "Velvet House", "Sunrise Media"]
    for i, n in enumerate(studs, 1):
        c.execute("INSERT OR REPLACE INTO studios(id,name,normalized_name) VALUES(?,?,?)", (i, n, n.lower()))
    tags = ["4k", "pov", "outdoor", "long-form", "compilation", "vertical", "hd", "interview"]
    for i, n in enumerate(tags, 1):
        c.execute("INSERT OR REPLACE INTO tags(id,name,normalized_name) VALUES(?,?,?)", (i, n, n))
    titles = ["Short", "A medium length title for a scene",
              "An extremely long scene title that goes on and on to test how the cards truncate multi line text in narrow columns and wide ones alike",
              "Scene 07", "Évènement spécial — 完整版", "S01E03 Finale (Uncut Director's Edition) [2160p]"]
    res = [(3840, 2160, "4K"), (1920, 1080, "1080p"), (1280, 720, "720p"), (720, 1280, "1080p"), (640, 480, "480p")]
    for i in range(1, 73):
        w, h, rc = random.choice(res)
        ch = chs[i % 4][0]
        d = iso(i * 3)
        c.execute("""INSERT OR REPLACE INTO media(id,source_channel,source_message_id,filename,title,size,duration,width,height,fps,codec,resolution_category,streamable,thumbnail_message_id,thumbnail_status,upload_date,favorite,status,created_at,updated_at,identification_state,orientation,site_name)
                     VALUES(?,?,?,?,?,?,?,?,?,30,'h264',?,1,?, 'READY',?,?, 'ACTIVE',?,?, 'MATCHED',?,?)""",
                  (i, ch, 1000 + i, f"file_{i}.mp4", random.choice(titles), random.randint(200_000_000, 9_000_000_000), random.choice([95, 640, 1800, 3725, 7300]),
                   w, h, rc, 1000 + i, d, 1 if i % 9 == 0 else 0, d, d, "portrait" if h > w else "landscape", random.choice(studs)))
        for p in random.sample(range(1, 13), random.randint(0, 3)):
            c.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id,confidence) VALUES(?,?,1)", (i, p))
        for s in random.sample(range(1, 7), random.randint(0, 1)):
            c.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,1)", (i, s))
        for t in random.sample(range(1, 9), random.randint(0, 4)):
            c.execute("INSERT OR IGNORE INTO video_tags(video_id,tag_id,confidence) VALUES(?,?,1)", (i, t))
    # Half of the library is SFW, so the SFW pass of the parity run lists real items and the rows of the other mode have to stay hidden.
    # Everything above is NSFW by default; these are the SFW ones (two channels with their videos and photos, two performers' worth of
    # names, two studios and two tags).
    c.execute("UPDATE channels SET content_rating='sfw' WHERE telegram_id IN ('-1003','-1004')")
    c.execute("UPDATE performers SET content_rating='sfw' WHERE id IN (7,8,11,12)")
    c.execute("UPDATE studios SET content_rating='sfw' WHERE id IN (5,6)")
    c.execute("UPDATE tags SET content_rating='sfw' WHERE id IN (7,8)")


def vec(rng):
    v = [rng.gauss(0, 1) for _ in range(128)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def seed_photos(c, data_dir: Path):
    from PIL import Image, ImageDraw
    random.seed(3)
    cache = data_dir / "cache" / "photo_library"
    for d in ("originals", "thumbnails", "previews"):
        (cache / d).mkdir(parents=True, exist_ok=True)
    c.execute("DELETE FROM photo_library_items")
    c.execute("DELETE FROM photo_library_scan_state")
    sizes = [(1200, 800), (800, 1200), (1000, 1000), (1600, 900), (900, 1400), (1100, 740)]
    n = 0
    for ch in ["-1001", "-1002", "-1003"]:
        for m in range(1, 17 if ch == "-1001" else 9):
            w, h = sizes[(m + n) % len(sizes)]
            n += 1
            k = hashlib.sha256(f"{ch}:{m}".encode()).hexdigest()
            im = Image.new("RGB", (w, h), tuple(random.randint(40, 200) for _ in range(3)))
            dr = ImageDraw.Draw(im)
            dr.rectangle([w // 8, h // 8, w * 7 // 8, h * 7 // 8], outline=(255, 255, 255), width=8)
            dr.text((w // 2 - 30, h // 2), f"{ch}/{m}", fill=(255, 255, 255))
            im.save(cache / "originals" / f"{k}.bin", "JPEG", quality=85)
            t = im.copy()
            t.thumbnail((360, 360))
            t.save(cache / "thumbnails" / f"{k}.jpg", "JPEG")
            c.execute("INSERT INTO photo_library_items(channel_id,message_id,filename,caption,mime_type,size,width,height,message_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (ch, m, f"IMG_{ch[-1]}{m:03d}.jpg", "Caption " + str(m), "image/jpeg", os.path.getsize(cache / "originals" / f"{k}.bin"), w, h, iso(m * 3 + n), iso(0), iso(0)))
    for ch, cnt in [("-1001", 16), ("-1002", 8), ("-1003", 8)]:
        c.execute("INSERT INTO photo_library_scan_state(channel_id,status,last_scan_at,messages_scanned,photos_found,updated_at) VALUES(?,?,?,?,?,?)", (ch, "COMPLETE", iso(0), cnt, cnt, iso(0)))

    for table in ("photo_face_groups", "photo_face_detections", "photo_face_analysis", "performer_face_embeddings"):
        c.execute(f"DELETE FROM {table}")
    crops = data_dir / "cache" / "photo_face_groups" / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    ids = [r[0] for r in c.execute("SELECT id FROM photo_library_items ORDER BY id")]
    rng = random.Random(11)
    base_vec = vec(rng)
    plan = [(1, "Face group 1", None, ids[0:4]), (2, "Face group 2", None, ids[4:7]), (3, "Face group 3", 1, ids[7:9])]
    ts = iso(0)
    for gid, label, perf, photos in plan:
        c.execute("INSERT INTO photo_face_groups(id,label,performer_id,created_at,updated_at) VALUES(?,?,?,?,?)", (gid, label, perf, ts, ts))
        for k, pid in enumerate(photos):
            crop = crops / f"g{gid}_{k}.jpg"
            Image.new("RGB", (160, 160), (40 * gid, 120, 200 - 30 * gid)).save(crop, "JPEG")
            emb = [x + rng.gauss(0, 0.03) for x in base_vec] if gid == 1 else vec(rng)
            c.execute("INSERT INTO photo_face_detections(photo_id,face_index,group_id,bbox_json,embedding_json,crop_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (pid, 0, gid, "[0,0,10,10]", json.dumps(emb), str(crop), ts, ts))
    for pid in ids[:9]:
        c.execute("INSERT INTO photo_face_analysis(photo_id,status,face_count,analyzed_at,updated_at) VALUES(?,?,?,?,?)", (pid, "COMPLETE", 1, ts, ts))
    c.execute("UPDATE photo_library_items SET caption='Mia Rivera shoot' WHERE id=?", (ids[4],))
    c.execute("INSERT INTO performer_face_embeddings VALUES(?,?,?,?)", (1, "k", json.dumps([x + rng.gauss(0, 0.03) for x in base_vec]), ts))
    c.execute("INSERT INTO performer_face_embeddings VALUES(?,?,?,?)", (2, "k", json.dumps(vec(rng)), ts))
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True, help="the Archive X data folder (ARCHIVE_X_DATA_DIR)")
    args = ap.parse_args()
    data = Path(args.data_dir).resolve()
    db_path = data / "database" / "archive.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    from telegram_archive.core.database import Database
    Database(db_path)           # creates the schema and runs the migrations when the file is new
    c = sqlite3.connect(db_path, timeout=30)
    seed_videos(c)
    photos = seed_photos(c, data)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    print(f"seeded {db_path}: {n} videos, {photos} photos")


if __name__ == "__main__":
    main()
