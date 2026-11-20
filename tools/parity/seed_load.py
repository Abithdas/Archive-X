"""Add a LARGE amount of data on top of the fixture, to measure where the Python backend is slow.

    python tools/parity/seed_load.py --data-dir /path/to/data     # after seed_fixture.py, with the server running

Adds 4,000 performers and 1,200 studios (with provider ids, long bios, images), 40,000 photos in one channel, 600 face groups
of 14 faces each with 128-number vectors, 300 performer face vectors, and links of the new performers and studios to videos.
The numbers are those of a big library; the fixture alone (72 videos, 32 photos) answers every route in a few milliseconds
and cannot show what matters.
"""
import argparse
import sys
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--data-dir", required=True)
args = ap.parse_args()
DB = str(Path(args.data_dir).resolve() / "database" / "archive.db")
if not Path(DB).exists():
    sys.exit(f"{DB} does not exist: run seed_fixture.py first")

import json
import math
import random
import sqlite3
c=sqlite3.connect(DB,timeout=60);c.row_factory=sqlite3.Row
random.seed(2);now='2025-06-01T00:00:00'
bio="She is an actress born in 1994 who started her career in 2016 and has appeared in many productions across several studios. "*5
for i in range(4000):
    n=f"Bulk Performer {i:05d}"
    c.execute("INSERT OR IGNORE INTO performers(name,image,profile,provider_ids,bio,gender,normalized_name,canonical_key,aliases) VALUES(?,?,?,?,?,?,?,?,?)",(n,f"https://img.example/p/{i}.jpg",f"https://stashdb.org/performers/{i}",json.dumps({'stashdb':f'id-{i}','theporndb':f'tp-{i}'}),bio,random.choice(['female','female','female','male','trans']),n.lower(),n.lower(),json.dumps([f'alias {i}','other alias'])))
for i in range(1200):
    n=f"Bulk Studio {i:04d}";c.execute("INSERT OR IGNORE INTO studios(name,provider_ids,normalized_name,canonical_key,aliases,image) VALUES(?,?,?,?,?,?)",(n,json.dumps({'stashdb':f's-{i}'}),n.lower(),n.lower(),'[]',f"https://img.example/s/{i}.jpg"))
base=c.execute("select coalesce(max(id),0) from photo_library_items").fetchone()[0]
rows=[(base+i+1,'-1002',100000+i,f"IMG_{i:06d}.jpg",'a caption '*5,'image/jpeg',random.randint(100000,4000000),4000,3000,f"2025-{1+i%12:02d}-{1+i%28:02d}T10:00:00",'telegram',f"h{i}",'', '', now,now,now) for i in range(40000)]
c.executemany("INSERT OR IGNORE INTO photo_library_items(id,channel_id,message_id,filename,caption,mime_type,size,width,height,message_date,source_kind,content_hash,local_path,local_sha256,cached_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
pids=[r[0] for r in c.execute("select id from performers order by id limit 400")]
def vec():
    v=[random.gauss(0,1) for _ in range(128)];n=math.sqrt(sum(x*x for x in v));return [x/n for x in v]
for g in range(600):
    gid=c.execute("INSERT INTO photo_face_groups(label,performer_id,created_at,updated_at) VALUES(?,?,?,?)",(f"Face group {g}",pids[g] if g<300 else None,now,now)).lastrowid
    for k in range(14):
        photo=base+1+(g*14+k)
        c.execute("INSERT OR REPLACE INTO photo_face_analysis(photo_id,status,face_count,updated_at) VALUES(?,?,?,?)",(photo,'COMPLETE',1,now))
        c.execute("INSERT OR IGNORE INTO photo_face_detections(photo_id,face_index,group_id,embedding_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",(photo,0,gid,json.dumps(vec()),now,now))
for pid in pids[:300]:
    c.execute("INSERT OR REPLACE INTO performer_face_embeddings VALUES(?,?,?,?)",(pid,'k',json.dumps(vec()),now))
random.seed(4)
lib=[r[0] for r in c.execute("select id from archive_x_library_media")]
for pid in [r[0] for r in c.execute("select id from performers where name like 'Bulk Performer%'")]:
    for v in random.sample(lib,random.randint(1,3)): c.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)",(v,pid))
for sid in [r[0] for r in c.execute("select id from studios where name like 'Bulk Studio%'")]:
    for v in random.sample(lib,random.randint(1,2)): c.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id) VALUES(?,?)",(v,sid))
c.commit();print('bulk data ready')
