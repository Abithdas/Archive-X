"""What moving one video from a channel to another does today: copy, picture, verify, delete the source. These pin the behaviour down so the faster
version (batched verification, pictures uploaded while the video copies) can be shown to change nothing but the time."""
import pytest

import transfer_harness as t
from telegram_archive.media_manager import telegram_media_manager as tmm


@pytest.fixture
def world(monkeypatch):
    client = t.FakeClient()
    rec = t.install(monkeypatch)
    return client, rec, t.new_state()


def test_a_video_is_copied_with_its_picture_right_after_it_and_the_source_is_removed(world):
    client, rec, state = world
    msg = client.add_source_video("scene one.mp4")
    assert t.run(t.move_one(client, msg, state)) is True
    posted = [p for p in client.posts if p[0] == t.DEST_ID]
    assert [p[2] for p in posted] == ["video", "thumbnail"] and posted[1][1] == posted[0][1] + 1       # adjacent: the picture is the next message
    assert msg.id not in client.chats[t.SOURCE_ID].messages                                              # the source is gone
    assert t.entry(state, msg)["status"] == "COMPLETE" and t.entry(state, msg)["source_deleted"] is True
    assert rec.recorded == [(msg.id, posted[0][1], posted[1][1])]


def test_the_copy_uses_the_telegram_side_media_not_an_upload_of_the_video(world):
    client, _, state = world
    msg = client.add_source_video("scene one.mp4")
    t.run(t.move_one(client, msg, state))
    assert [c[2] for c in client.calls("send_file")] == ["media", "uploaded"]   # the video by reference, the picture from the sheet that was already uploaded
    assert [c[0] for c in client.log if c[0] in ("upload_file", "upload_media")] == ["upload_file", "upload_media"]   # the contact sheet is uploaded once, registered as the cover


def test_a_destination_without_the_picture_keeps_the_source(world):
    client, rec, state = world
    msg = client.add_source_video("scene two.mp4")
    original = client.send_file

    async def drop_picture(entity, file, **kw):
        out = await original(entity, file, **kw)
        if getattr(out, "document", None) is not None and out.video is None:
            client.chats[t.DEST_ID].messages.pop(out.id)                          # Telegram never shows the picture
        return out
    client.send_file = drop_picture
    with pytest.raises(RuntimeError, match="Destination verification failed"):
        t.run(t.move_one(client, msg, state))
    assert msg.id in client.chats[t.SOURCE_ID].messages                          # nothing was deleted
    assert t.entry(state, msg)["status"] == "DESTINATION_VERIFY_FAILED" and not rec.recorded


def test_a_source_that_is_still_there_after_the_delete_is_an_error_and_not_complete(world):
    client, _, state = world
    msg = client.add_source_video("scene three.mp4")

    async def refuse(entity, ids):
        client.log.append(("delete_messages", int(getattr(entity, "id", entity)), tuple(ids)))      # the call "succeeds", the message stays
    client.delete_messages = refuse
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        t.run(t.move_one(client, msg, state))
    assert t.entry(state, msg).get("status") != "COMPLETE" and not t.entry(state, msg).get("source_deleted")


def test_an_item_whose_copy_already_exists_is_not_posted_again(world):
    client, rec, state = world
    msg = client.add_source_video("scene four.mp4")
    t.run(t.move_one(client, msg, state))                                       # a first run copied it and removed the source
    again = client.add_source_video("scene four.mp4")
    e = t.entry(state, msg)
    state["items"][tmm.media_mode_key(t.peer(t.SOURCE_ID), t.peer(t.DEST_ID), again.id, "VIDEO", "VIDEO")] = {
        "destination_video_id": e["destination_video_id"], "destination_thumbnail_id": e["destination_thumbnail_id"], "thumbnail_kind": "playback-contact-sheet-36"}
    posted = len(client.posts)
    assert t.run(t.move_one(client, again, state)) is True
    assert len(client.posts) == posted                                           # nothing new in the destination; the source of the second is removed
    assert again.id not in client.chats[t.SOURCE_ID].messages


def test_a_flood_wait_on_the_copy_waits_and_tries_again(world, monkeypatch):
    client, _, state = world
    msg = client.add_source_video("scene five.mp4")

    class Flood(Exception):
        seconds = 3
    waited = []

    async def no_sleep(s):
        waited.append(s)
    monkeypatch.setattr(tmm.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(tmm, "flood_wait_seconds", lambda exc: 3 if isinstance(exc, Flood) else 0)
    client.fail["send_file"] = [Flood()]
    assert t.run(t.move_one(client, msg, state)) is True
    assert 4 in waited                                                          # the 3 s Telegram asked for + the 1 s margin the send helper adds
    assert [p[2] for p in client.posts if p[0] == t.DEST_ID] == ["video", "thumbnail"]
