"""A fake Telegram for the channel-to-channel mover (media_manager.mm_vertical_mover), with real Telethon attribute types so the module's own
helpers (orientation, signature, name matching) run unpatched. Only what reaches the database or reads Telegram history is stubbed."""
from __future__ import annotations

import asyncio
import datetime
import io
import itertools
from types import SimpleNamespace

from telethon import functions, types

from telegram_archive.media_manager import telegram_media_manager as tmm

SOURCE_ID = -1001000000001
DEST_ID = -1001000000002
COVER = b"\xff\xd8\xff\xe0contact-sheet-bytes\xff\xd9"


_DOC_IDS = itertools.count(7000)


def _video_doc(name, w, h, dur):
    return types.Document(
        id=next(_DOC_IDS), access_hash=1, file_reference=b"x", date=datetime.datetime(2026, 1, 1), mime_type="video/mp4", size=1000, dc_id=1,
        attributes=[types.DocumentAttributeVideo(duration=dur, w=w, h=h), types.DocumentAttributeFilename(name)])


def _message(chat_id, mid, *, video=None, document=None, name="", caption=""):
    doc = video or document
    return SimpleNamespace(
        id=mid, chat_id=chat_id, video=video, document=doc, photo=None, message=caption,
        media=SimpleNamespace(kind="media-handle", name=name, doc=doc),
        file=SimpleNamespace(name=name, size=1000, duration=getattr(doc, "attributes", [SimpleNamespace(duration=0)])[0].duration if video else 0,
                             width=getattr(doc.attributes[0], "w", 0) if video else 0, height=getattr(doc.attributes[0], "h", 0) if video else 0),
    )


def video_message(chat_id, mid, name="clip.mp4", w=720, h=1280, dur=30.0):
    return _message(chat_id, mid, video=_video_doc(name, w, h, dur), name=name)


class Chat:
    def __init__(self, first_id=100):
        self.messages: dict[int, SimpleNamespace] = {}
        self._ids = itertools.count(first_id)

    def add(self, make):
        mid = next(self._ids)
        self.messages[mid] = make(mid)
        return self.messages[mid]


class FakeClient:
    """`latency` makes every call take that long, so overlap between calls is visible. `log` records every call as (method, chat id)."""

    def __init__(self, *, latency=0.0):
        self.chats = {SOURCE_ID: Chat(500), DEST_ID: Chat(9000)}
        self.latency = latency
        self.log: list[tuple] = []
        self.fail: dict[str, list] = {}
        self.posts: list[tuple] = []          # (chat id, message id, kind) in posting order
        self.hold_back = set()                # message ids that a read-back does not return (Telegram has not shown them)
        self.uploaded: dict[int, dict] = {}   # upload id -> {"name", "used_as": [...]}: what each uploaded file was used for
        self.cover_uploads: list[int] = []
        self.single_use = False               # True: an uploaded file can be used once only (to see what the mover does when reuse fails)
        self.documents: dict[int, types.Document] = {}
        self.names: dict[int, str] = {}
        self._upload_ids = itertools.count(1)

    # ------------------------------------------------------------------ helpers
    def _chat(self, entity):
        return self.chats[int(getattr(entity, "id", entity))]

    async def _call(self, name, entity, *detail):
        self.log.append((name, int(getattr(entity, "id", entity)), *detail))
        if self.latency:
            await asyncio.sleep(self.latency)
        queue = self.fail.get(name)
        if queue:
            exc = queue.pop(0)
            if exc is not None:
                raise exc

    def calls(self, name, chat=None):
        return [c for c in self.log if c[0] == name and (chat is None or c[1] == chat)]

    def add_source_video(self, name, **kw):
        msg = self.chats[SOURCE_ID].add(lambda mid: video_message(SOURCE_ID, mid, name, **kw))
        self.documents[msg.document.id] = msg.document
        self.names[msg.document.id] = name
        return msg

    # ------------------------------------------------------------------ the Telegram surface the mover uses
    async def get_messages(self, entity, ids=None, **kw):
        many = isinstance(ids, (list, tuple))
        await self._call("get_messages", entity, tuple(ids) if many else ids)
        chat = self._chat(entity)

        def one(i):
            return None if int(i) in self.hold_back and chat is self.chats[DEST_ID] else chat.messages.get(int(i))
        return [one(i) for i in ids] if many else one(ids)

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, request, *a, **kw):
        if isinstance(request, functions.messages.UploadMediaRequest):
            await self._call("upload_media", request.peer, type(request.media).__name__)
            handle = request.media.file
            if getattr(handle, "kind", "") != "uploaded":
                raise RuntimeError("the cover was not an uploaded file")
            self._use(handle, "cover")
            self.cover_uploads.append(handle.id)
            return SimpleNamespace(photo=types.Photo(id=handle.id, access_hash=1, file_reference=b"x", date=datetime.datetime(2026, 1, 1), sizes=[], dc_id=1))
        raise NotImplementedError(type(request).__name__)

    async def upload_file(self, file, *, file_name=None, **kw):
        await self._call("upload_file", DEST_ID, file_name or getattr(file, "name", ""))
        handle = SimpleNamespace(kind="uploaded", id=next(self._upload_ids), name=file_name or getattr(file, "name", ""))
        self.uploaded[handle.id] = {"name": handle.name, "used_as": []}
        return handle

    def _use(self, handle, how):
        entry = self.uploaded[handle.id]
        entry["used_as"].append(how)
        if self.single_use and len(entry["used_as"]) > 1:
            raise RuntimeError("FILE_ID_INVALID: the uploaded file was already used")

    async def send_file(self, entity, file, *, force_document=False, caption=None, attributes=None, **kw):
        chat_id = int(getattr(entity, "id", entity))
        if isinstance(file, types.InputMediaDocument):                         # an existing video re-sent by reference, with the custom cover
            await self._call("send_file", entity, "media")
            doc = self.documents[int(file.id.id)]
            cover = file.video_cover
            msg = self._chat(entity).add(lambda mid: SimpleNamespace(
                id=mid, chat_id=chat_id, video=doc, document=doc, photo=None, message=caption or "",
                media=SimpleNamespace(video_cover=cover, document=doc), file=SimpleNamespace(name=self.names[doc.id], size=1000, duration=30.0, width=720, height=1280)))
            self.posts.append((chat_id, msg.id, "video"))
            return msg
        kind = "uploaded" if getattr(file, "kind", "") == "uploaded" else "bytes"
        if kind == "bytes":                                                    # Telethon uploads the parts inside send_file
            await self._call("upload_file", DEST_ID, getattr(file, "name", ""))
        await self._call("send_file", entity, kind)
        if kind == "uploaded":
            self._use(file, "document")
        name = file.name if kind == "uploaded" else getattr(file, "name", "")
        if attributes:
            name = next((a.file_name for a in attributes if isinstance(a, types.DocumentAttributeFilename)), name)
        doc = SimpleNamespace(mime_type="image/jpeg", attributes=[types.DocumentAttributeFilename(name)], size=len(COVER))
        msg = self._chat(entity).add(lambda mid: SimpleNamespace(id=mid, chat_id=chat_id, video=None, document=doc, photo=None, message="",
                                                              media=None, file=SimpleNamespace(name=name, size=len(COVER), duration=0, width=0, height=0)))
        self.posts.append((chat_id, msg.id, "thumbnail"))
        return msg

    async def delete_messages(self, entity, ids):
        await self._call("delete_messages", entity, tuple(ids))
        chat = self._chat(entity)
        for i in ids:
            chat.messages.pop(int(i), None)


def peer(chat_id):
    return SimpleNamespace(id=chat_id)


class Recorder:
    """What the database-facing helpers were asked to do, in order."""

    def __init__(self):
        self.recorded, self.snapshots, self.synced, self.cleaned, self.thumb_deleted = [], [], [], [], []


def install(monkeypatch, *, cover=COVER, source_thumbnail=0):
    """Stub what reaches the database or reads Telegram history; everything else is the real module."""
    rec = Recorder()

    async def select(client, source, message, td, **kw):
        return cover, "playback-contact-sheet-36", None, None

    async def caption(db, source, message):
        return "caption"

    async def resolve(client, source, video, db=None, *, known_message_id=None):
        return source_thumbnail

    async def record(db, source, message, destination, video_id, thumb_id, **kw):
        rec.recorded.append((int(message.id), int(video_id), int(thumb_id)))

    async def store_snapshot(db, destination, video_id, thumb_id, **kw):
        rec.snapshots.append((int(video_id), int(thumb_id)))

    async def sync_registry(db, client, destination, video_id, thumb_id, **kw):
        rec.synced.append((int(video_id), int(thumb_id)))
        return {"registered": True, "prepared": False}

    monkeypatch.setattr(tmm, "_select_manager_thumbnail_with_retry", select)
    monkeypatch.setattr(tmm, "_prepare_transfer_catalogue_caption", caption)
    monkeypatch.setattr(tmm, "_resolve_associated_source_thumbnail_for_move", resolve)
    monkeypatch.setattr(tmm, "_record_verified_transfer_location", record)
    monkeypatch.setattr(tmm, "_store_delivery_representative_snapshot", store_snapshot)
    monkeypatch.setattr(tmm, "_sync_verified_transfer_destination_registry", sync_registry)
    monkeypatch.setattr(tmm, "_cleanup_verified_delivery_artifacts", lambda *a, **k: rec.cleaned.append(a[1].id if len(a) > 1 else None))
    monkeypatch.setattr(tmm, "_mark_moved_source_thumbnail_deleted", lambda db, source, tid: rec.thumb_deleted.append(int(tid)))
    return rec


def new_state():
    return {"items": {}, "metadata_prefetch": {}}


def run(coro):
    return asyncio.run(coro)


def move_one(client, message, state, **kw):
    """The mover's per-item function, as the job calls it."""
    return tmm._media_process_one_unlocked(
        client, peer(SOURCE_ID), peer(DEST_ID), message, state, "VIDEO", "VIDEO",
        db=None, orientation="BOTH", cover_policy="EXISTING", save_state_fn=lambda s: None, **kw)


def entry(state, message):
    return state["items"][tmm.media_mode_key(peer(SOURCE_ID), peer(DEST_ID), message.id, "VIDEO", "VIDEO")]
