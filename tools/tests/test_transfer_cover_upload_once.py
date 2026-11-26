"""The contact sheet is uploaded ONCE per transferred video and used for both the video's cover and the separate picture; if Telegram refuses the
second use, the mover goes back to uploading each time and never creates a duplicate."""
import asyncio
import io

import pytest
from telethon import types
from telethon.client import uploads

import transfer_harness as t
from telegram_archive.engines import telegram_sender
from telegram_archive.engines.transfer_timing import TIMER
from telegram_archive.media_manager import telegram_media_manager as tmm


@pytest.fixture(autouse=True)
def fresh_state():
    telegram_sender.CoverReuse.reset()
    TIMER.reset()
    yield
    telegram_sender.CoverReuse.reset()


@pytest.fixture
def world(monkeypatch):
    client = t.FakeClient()
    rec = t.install(monkeypatch)
    return client, rec, t.new_state()


def picture_posts(client):
    return [p for p in client.posts if p[0] == t.DEST_ID and p[2] == "thumbnail"]


def test_one_upload_serves_the_cover_and_the_picture(world):
    client, _, state = world
    msg = client.add_source_video("scene one.mp4")
    assert t.run(t.move_one(client, msg, state)) is True
    assert len(client.calls("upload_file")) == 1                                   # was two
    (entry,) = client.uploaded.values()
    assert entry["used_as"] == ["cover", "document"]                              # the same upload, both uses
    assert [p[2] for p in client.posts] == ["video", "thumbnail"]
    assert (TIMER.reuse_ok, TIMER.reuse_refused) == (1, 0)


def test_the_picture_keeps_its_file_name_and_is_still_posted_after_the_video(world):
    client, _, state = world
    msg = client.add_source_video("scene one.mp4")
    t.run(t.move_one(client, msg, state))
    video_id, picture_id = [p[1] for p in client.posts]
    picture = client.chats[t.DEST_ID].messages[picture_id]
    assert picture.file.name == "scene one.jpg" and picture_id == video_id + 1 and picture.video is None and picture.document is not None


def test_when_telegram_refuses_the_second_use_the_sheet_is_uploaded_again_and_the_item_still_completes(world):
    client, _, state = world
    client.single_use = True
    msg = client.add_source_video("scene one.mp4")
    assert t.run(t.move_one(client, msg, state)) is True
    assert len(client.calls("upload_file")) == 2                                   # the shared upload, then the second upload that replaced it
    assert [p[2] for p in client.posts] == ["video", "thumbnail"] and len(picture_posts(client)) == 1      # one picture, no duplicate
    assert telegram_sender.CoverReuse.enabled is False and (TIMER.reuse_ok, TIMER.reuse_refused) == (0, 1)
    assert msg.id not in client.chats[t.SOURCE_ID].messages


def test_after_one_refusal_the_next_videos_do_not_try_to_share_again(world):
    client, _, state = world
    client.single_use = True
    first, second = client.add_source_video("a.mp4"), client.add_source_video("b.mp4")
    t.run(t.move_one(client, first, state))
    before = len(client.calls("upload_file"))
    t.run(t.move_one(client, second, state))
    assert len(client.calls("upload_file")) - before == 2                          # plain: one upload for the cover, one for the picture, as before
    assert all(len(v["used_as"]) == 1 for v in list(client.uploaded.values())[before:])
    assert TIMER.reuse_refused == 1                                                # asked once, not again


def test_an_error_that_is_not_a_refusal_is_not_retried_with_a_new_upload(world):
    client, _, state = world
    msg = client.add_source_video("scene one.mp4")
    client.fail["send_file"] = [None, ConnectionError("the connection dropped")]    # the video goes; the picture's send fails in an unknown way
    with pytest.raises(ConnectionError):
        t.run(t.move_one(client, msg, state))
    assert len(picture_posts(client)) == 0 and telegram_sender.CoverReuse.enabled is True       # nothing duplicated, reuse left alone
    assert msg.id in client.chats[t.SOURCE_ID].messages                              # and the source stays


def test_if_the_early_upload_fails_everything_goes_as_before(world):
    client, _, state = world
    msg = client.add_source_video("scene one.mp4")
    client.fail["upload_file"] = [ConnectionError("offline")]                     # the early upload
    assert t.run(t.move_one(client, msg, state)) is True
    assert len(client.calls("upload_file")) == 3                                   # the failed early one, the cover's own, the picture's own
    assert [p[2] for p in client.posts] == ["video", "thumbnail"] and (TIMER.reuse_ok, TIMER.reuse_refused) == (0, 0)


def test_a_flood_wait_on_the_picture_waits_and_posts_the_same_upload(world, monkeypatch):
    client, _, state = world
    msg = client.add_source_video("scene one.mp4")

    class Flood(Exception):
        seconds = 7
    waited = []

    async def no_sleep(s):
        waited.append(s)
    monkeypatch.setattr(tmm.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(tmm, "flood_wait_seconds", lambda exc: 7 if isinstance(exc, Flood) else 0)
    client.fail["send_file"] = [None, Flood()]
    assert t.run(t.move_one(client, msg, state)) is True
    assert 9 in waited and len(client.calls("upload_file")) == 1                   # 7 s + 2 s, and no new upload
    assert len(picture_posts(client)) == 1 and TIMER.flood_count >= 1 and TIMER.flood_seconds >= 7


def test_nothing_is_uploaded_when_the_whole_pair_is_already_in_the_destination(world):
    client, _, state = world
    first = client.add_source_video("scene one.mp4")
    t.run(t.move_one(client, first, state))
    done = t.entry(state, first)
    again = client.add_source_video("scene one.mp4")
    state["items"][tmm.media_mode_key(t.peer(t.SOURCE_ID), t.peer(t.DEST_ID), again.id, "VIDEO", "VIDEO")] = {
        "destination_video_id": done["destination_video_id"], "destination_thumbnail_id": done["destination_thumbnail_id"], "thumbnail_kind": "playback-contact-sheet-36"}
    uploads = len(client.calls("upload_file"))
    assert t.run(t.move_one(client, again, state)) is True
    assert len(client.calls("upload_file")) == uploads


def test_telegram_refusal_detection_names_only_refusals():
    refused = [RuntimeError("FILE_ID_INVALID"), RuntimeError("The file parts are invalid (FILE_PARTS_INVALID)"), type("FilePartMissingError", (Exception,), {})("x"), RuntimeError("MEDIA_INVALID")]
    other = [ConnectionError("dropped"), TimeoutError(), RuntimeError("CHAT_WRITE_FORBIDDEN"), ValueError("bad")]
    assert all(telegram_sender.upload_was_refused(e) for e in refused)
    assert not any(telegram_sender.upload_was_refused(e) for e in other)


# ---------------------------------------------------------------- Telethon builds the same message from the shared upload as from the bytes ---------------
class _Uploader:
    """Just enough of a client for Telethon's own `_file_to_media`: uploading gives a handle named after the file."""

    async def upload_file(self, file, *, file_size=None, progress_callback=None, **kw):
        return types.InputFile(id=1, parts=1, name=getattr(file, "name", "x"), md5_checksum="")


def _media(file, **kw):
    async def go():
        return (await uploads.UploadMethods._file_to_media(_Uploader(), file, **kw))[1]
    return asyncio.run(go())


def test_an_uploaded_handle_makes_the_same_document_as_the_bytes_did():
    name = "scene one.jpg"
    payload = io.BytesIO(t.COVER)
    payload.name = name
    before = _media(payload, force_document=True)                                        # what upload_thumbnail_bytes_document sent
    handle = types.InputFile(id=1, parts=1, name="whatever.jpg", md5_checksum="")
    after = _media(handle, force_document=True, mime_type="image/jpeg", attributes=[types.DocumentAttributeFilename(file_name=name)])
    assert isinstance(before, types.InputMediaUploadedDocument) and isinstance(after, types.InputMediaUploadedDocument)
    assert after.mime_type == before.mime_type == "image/jpeg"
    names = lambda m: [a.file_name for a in m.attributes if isinstance(a, types.DocumentAttributeFilename)]
    assert names(after) == names(before) == [name]
    assert type(after) is type(before) and after.force_file == before.force_file


def test_when_the_cover_registration_is_refused_it_uploads_again_and_the_picture_does_not_try_the_refused_handle(world):
    client, _, state = world
    client.fail["upload_media"] = [RuntimeError("FILE_ID_INVALID")]                 # the first registration of the shared upload
    msg = client.add_source_video("scene one.mp4")
    assert t.run(t.move_one(client, msg, state)) is True
    assert len(client.calls("upload_media")) == 2                                   # refused, then registered from a fresh upload
    assert len(client.calls("upload_file")) == 3                                    # the early one, the cover's fresh one, the picture's own (the handle is not tried again)
    assert [p[2] for p in client.posts] == ["video", "thumbnail"] and len(picture_posts(client)) == 1
    assert telegram_sender.CoverReuse.enabled is False and TIMER.reuse_refused == 1 and TIMER.reuse_ok == 0


def test_an_error_that_is_not_a_refusal_when_registering_the_cover_is_not_swallowed(world, monkeypatch):
    client, _, state = world

    async def no_sleep(s):
        pass
    monkeypatch.setattr(tmm.asyncio, "sleep", no_sleep)
    client.fail["upload_media"] = [ConnectionError("dropped")] * 3                  # the send helper's own three attempts all fail
    msg = client.add_source_video("scene one.mp4")
    with pytest.raises(ConnectionError):
        t.run(t.move_one(client, msg, state))
    assert client.posts == [] and msg.id in client.chats[t.SOURCE_ID].messages and telegram_sender.CoverReuse.enabled is True


def test_a_single_hiccup_when_registering_the_cover_is_still_retried_as_before(world, monkeypatch):
    client, _, state = world

    async def no_sleep(s):
        pass
    monkeypatch.setattr(tmm.asyncio, "sleep", no_sleep)
    client.fail["upload_media"] = [ConnectionError("dropped")]
    msg = client.add_source_video("scene one.mp4")
    assert t.run(t.move_one(client, msg, state)) is True
    assert len(client.calls("upload_media")) == 2 and telegram_sender.CoverReuse.enabled is True and TIMER.reuse_ok == 1
