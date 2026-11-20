"""End-to-end test of POST /api/channel-maintenance/delete-duplicate with a fake Telegram client.
usage: python test_duplicate_delete_thumbnail.py <path-to-app-root>   (needs fastapi, telethon, httpx)"""
import os, sys, tempfile, datetime, asyncio, json
root = sys.argv[1]
tmp = tempfile.mkdtemp(prefix="axdup_")
os.environ.update(ARCHIVE_X_DATA_DIR=tmp, TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP="1", ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)
from telethon.tl import types as T
from fastapi.testclient import TestClient
from telegram_archive import config
from telegram_archive.app import create_app
from telegram_archive.core.auth import ensure_web_credentials
from telegram_archive.core.database import Database
from telegram_archive.engines.preview_prefetch import preview_frames_path

CH = -1001234567
now = datetime.datetime.now(datetime.timezone.utc)
def video(mid, doc=555, name="clip.mp4"):
    d = T.Document(id=doc, access_hash=1, file_reference=b"x", date=now, mime_type="video/mp4", size=1000, dc_id=2,
                   attributes=[T.DocumentAttributeVideo(duration=60, w=1280, h=720), T.DocumentAttributeFilename(name)])
    return T.Message(id=mid, peer_id=T.PeerChannel(abs(CH) % 10**10), date=now, message="", media=T.MessageMediaDocument(document=d))
def photo(mid):
    p = T.Photo(id=mid, access_hash=1, file_reference=b"", date=now, sizes=[T.PhotoSize("m", 100, 100, 1000)], dc_id=2)
    return T.Message(id=mid, peer_id=T.PeerChannel(abs(CH) % 10**10), date=now, message="", media=T.MessageMediaPhoto(photo=p))

class FakeClient:
    def __init__(self, msgs): self.msgs = msgs; self.deleted = []
    async def get_entity(self, i): return f"entity:{i}"
    async def get_messages(self, entity, ids=None):
        if isinstance(ids, (list, tuple)): return [self.msgs.get(int(i)) for i in ids]
        return self.msgs.get(int(ids))
    async def delete_messages(self, entity, ids): self.deleted.append(sorted(ids)); [self.msgs.pop(int(i), None) for i in ids]; return True

db = Database(); ensure_web_credentials(db)
app = create_app(db=db)
c = TestClient(app, base_url="https://testserver")
H = {"Origin": "https://testserver"}
assert c.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse-battery", "confirmation": "correct-horse-battery"}, headers=H).status_code == 200
assert c.post("/login", json={"username": "admin", "password": "correct-horse-battery"}, headers=H).status_code == 200

def add_media(mid, thumb=None, name="clip.mp4"):
    with db.lock:
        db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,size,duration,width,height,oshash,thumbnail_message_id,status,created_at,updated_at) "
                        "VALUES(?,?,?,?,1000,60,1280,720,'0123456789abcdef',?, 'ACTIVE','2026-01-01','2026-01-01')", (str(CH), mid, name, name, thumb))
        db.conn.commit()
    return db.conn.execute("SELECT id FROM media WHERE source_message_id=?", (mid,)).fetchone()["id"]

def run(label, msgs, target, keep, thumb_link=None, extra=None, local_files=True):
    fake = FakeClient(msgs)
    app.state.telegram.client = fake
    app.state.telegram.connected = True
    frames = preview_frames_path(CH, target)
    if local_files:
        frames.mkdir(parents=True, exist_ok=True); (frames / "frame_00.jpg").write_bytes(b"x")
    if extra: extra()
    r = c.post("/api/channel-maintenance/delete-duplicate", headers=H, json={
        "confirm_delete": True, "channel_id": CH, "message_id": target, "keep_message_id": keep, "keep_channel_id": CH})
    j = r.json()
    print(f"[{label}] http={r.status_code} deleted_calls={fake.deleted} thumbnail_deleted={j.get('thumbnail_deleted')} "
          f"source={j.get('thumbnail_source')} local_removed={j.get('local_assets_removed')} frames_left={frames.exists()} warn={j.get('warnings')}")
    return r.status_code, fake, j, frames

# A: no saved link; the contact sheet is the photo posted right after the video (the reported bug)
add_media(10); add_media(20)
sc, fake, j, frames = run("A no saved link", {10: video(10), 11: photo(11), 20: video(20), 21: photo(21)}, 10, 20)
# B: saved link on the media row
add_media(30, thumb=31); add_media(40)
run("B saved link", {30: video(30), 31: photo(31), 40: video(40)}, 30, 40)
# C: thumbnail shared with another active video -> must be kept
add_media(50, thumb=52); add_media(60, thumb=52)
run("C shared thumb kept", {50: video(50), 52: photo(52), 60: video(60)}, 50, 60)
# D: next message is another video -> no companion, only the video goes
add_media(70); add_media(80)
run("D no companion", {70: video(70), 71: video(71, doc=999, name="other.mp4"), 80: video(80)}, 70, 80)
