"""Verifying and deleting the sources of a group of posted videos together: the same safety as one at a time, with far fewer Telegram calls."""
import asyncio
import os
import tempfile

import pytest

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


def setup(monkeypatch, n=5, **install_kw):
    client = t.FakeClient()
    rec = t.install(monkeypatch, **install_kw)
    state = t.new_state()
    messages = [client.add_source_video(f"scene {i}.mp4") for i in range(1, n + 1)]
    return client, rec, state, messages


async def post_all(client, state, messages):
    return [await tmm._media_post_pair(
        client, t.peer(t.SOURCE_ID), t.peer(t.DEST_ID), m, state, "VIDEO", "VIDEO",
        db=None, orientation="BOTH", cover_policy="EXISTING", save_state_fn=lambda s: None) for m in messages]


async def finish(client, state, pendings):
    return await tmm._media_finish_moves(
        client, t.peer(t.SOURCE_ID), t.peer(t.DEST_ID), pendings, state, "VIDEO", "VIDEO", db=None, save_state_fn=lambda s: None)


def move_group(client, state, messages):
    async def go():
        pendings = await post_all(client, state, messages)
        return pendings, await finish(client, state, pendings)
    return t.run(go())


def many(client, name, chat):
    return [c for c in client.calls(name, chat) if isinstance(c[2], tuple)]


def test_a_group_is_verified_and_removed_with_one_read_one_delete_and_one_confirmation(monkeypatch):
    client, rec, state, messages = setup(monkeypatch, 5)
    _, results = move_group(client, state, messages)
    assert results == [None] * 5
    assert [len(c[2]) for c in many(client, "get_messages", t.DEST_ID)] == [10]            # five pairs in one read
    assert [len(c[2]) for c in many(client, "delete_messages", t.SOURCE_ID)] == [5]        # five sources in one call
    assert [len(c[2]) for c in many(client, "get_messages", t.SOURCE_ID)] == [5]           # confirmed in one read
    assert all(m.id not in client.chats[t.SOURCE_ID].messages for m in messages)
    assert all(t.entry(state, m)["status"] == "COMPLETE" and t.entry(state, m)["source_deleted"] for m in messages)
    assert len(rec.recorded) == 5 and TIMER.items == 5 and TIMER.failed == 0


def test_the_pairs_stay_in_order_each_picture_right_after_its_video(monkeypatch):
    client, _, state, messages = setup(monkeypatch, 4)
    move_group(client, state, messages)
    kinds = [p[2] for p in client.posts if p[0] == t.DEST_ID]
    ids = [p[1] for p in client.posts if p[0] == t.DEST_ID]
    assert kinds == ["video", "thumbnail"] * 4 and ids == list(range(ids[0], ids[0] + 8))


def test_far_fewer_calls_than_one_at_a_time(monkeypatch):
    one_by_one = setup(monkeypatch, 10)
    for m in one_by_one[3]:
        t.run(t.move_one(one_by_one[0], m, one_by_one[2]))
    separate = len(one_by_one[0].log)
    client, _, state, messages = setup(monkeypatch, 10)
    move_group(client, state, messages)
    assert len(client.log) < separate - 20, (len(client.log), separate)                    # 10 videos: more than 20 calls fewer


def test_a_pair_that_does_not_verify_keeps_its_source_and_does_not_stop_the_others(monkeypatch):
    client, rec, state, messages = setup(monkeypatch, 5)

    async def go():
        pendings = await post_all(client, state, messages)
        client.chats[t.DEST_ID].messages.pop(pendings[2].dest_thumb_id)                    # Telegram never shows the third picture
        return pendings, await finish(client, state, pendings)
    pendings, results = t.run(go())
    assert isinstance(results[2], RuntimeError) and "Destination verification failed" in str(results[2])
    assert [r for i, r in enumerate(results) if i != 2] == [None] * 4
    assert messages[2].id in client.chats[t.SOURCE_ID].messages                               # its source is untouched
    assert all(m.id not in client.chats[t.SOURCE_ID].messages for i, m in enumerate(messages) if i != 2)
    deleted = [i for c in many(client, "delete_messages", t.SOURCE_ID) for i in c[2]]
    assert messages[2].id not in deleted and len(deleted) == 4                                # never even asked for
    assert t.entry(state, messages[2])["status"] == "DESTINATION_VERIFY_FAILED" and len(rec.recorded) == 4


def test_a_video_whose_records_cannot_be_made_is_not_deleted(monkeypatch):
    client, rec, state, messages = setup(monkeypatch, 4)
    bad = messages[3].id

    async def record(db, source, message, destination, video_id, thumb_id, **kw):
        if int(message.id) == bad:
            raise RuntimeError("the database is busy")
        rec.recorded.append((int(message.id), int(video_id), int(thumb_id)))
    monkeypatch.setattr(tmm, "_record_verified_transfer_location", record)
    _, results = move_group(client, state, messages)
    assert isinstance(results[3], RuntimeError) and results[:3] == [None] * 3
    assert bad in client.chats[t.SOURCE_ID].messages and len(rec.recorded) == 3
    assert bad not in [i for c in many(client, "delete_messages", t.SOURCE_ID) for i in c[2]]


def test_if_the_group_delete_fails_the_sources_are_deleted_one_at_a_time(monkeypatch):
    client, _, state, messages = setup(monkeypatch, 4)
    client.fail["delete_messages"] = [RuntimeError("Telegram hiccup")]                     # the group call only
    _, results = move_group(client, state, messages)
    assert results == [None] * 4 and all(m.id not in client.chats[t.SOURCE_ID].messages for m in messages)
    assert len(client.calls("delete_messages", t.SOURCE_ID)) == 1 + 4                      # the failed group call, then one for each


def test_a_source_that_telegram_did_not_remove_is_an_error_for_that_video_only(monkeypatch):
    client, _, state, messages = setup(monkeypatch, 4)
    stubborn = messages[1].id
    original = client.delete_messages

    async def keep_one(entity, ids):
        await original(entity, [i for i in ids if int(i) != stubborn])                     # the call "succeeds", this message stays
    client.delete_messages = keep_one
    _, results = move_group(client, state, messages)
    assert isinstance(results[1], RuntimeError) and "could not be confirmed" in str(results[1]) and str(stubborn) in str(results[1])
    assert results[0] is None and results[2] is None and results[3] is None
    assert t.entry(state, messages[1]).get("status") != "COMPLETE" and not t.entry(state, messages[1]).get("source_deleted")


def test_a_failed_confirmation_read_falls_back_to_one_at_a_time_and_still_finishes(monkeypatch):
    client, _, state, messages = setup(monkeypatch, 3)
    original = client.get_messages
    calls = {"n": 0}

    async def flaky(entity, ids=None, **kw):
        if int(getattr(entity, "id", entity)) == t.SOURCE_ID and isinstance(ids, (list, tuple)) and len(ids) == 3:
            calls["n"] += 1
            raise ConnectionError("read failed")                                           # only the group's confirmation read
        return await original(entity, ids=ids, **kw)
    client.get_messages = flaky
    _, results = move_group(client, state, messages)
    assert results == [None] * 3 and calls["n"] == 1


def test_a_video_with_its_own_source_picture_deletes_both_in_the_same_call(monkeypatch):
    client, rec, state, messages = setup(monkeypatch, 3, source_thumbnail=777)
    _, results = move_group(client, state, messages)
    assert results == [None] * 3
    (group,) = many(client, "delete_messages", t.SOURCE_ID)
    assert sorted(group[2]) == sorted([m.id for m in messages] + [777] * 3) or set(group[2]) == {m.id for m in messages} | {777}
    assert rec.thumb_deleted.count(777) == 3


def test_posted_videos_that_were_never_finished_are_finished_by_the_next_run_without_posting_again(monkeypatch):
    client, rec, state, messages = setup(monkeypatch, 3)

    async def crash_before_finishing():
        pendings = await post_all(client, state, messages)
        for p in pendings:
            p.workdir.cleanup()                                                            # the process ended here: posted, nothing finished
    t.run(crash_before_finishing())
    posted = len(client.posts)
    assert all(t.entry(state, m)["status"] == "PAIR_COPIED" for m in messages) and all(m.id in client.chats[t.SOURCE_ID].messages for m in messages)
    for m in messages:                                                                      # the next run
        assert t.run(t.move_one(client, m, state)) is True
    assert len(client.posts) == posted                                                      # nothing posted a second time
    assert all(m.id not in client.chats[t.SOURCE_ID].messages for m in messages) and len(rec.recorded) == 3


def test_working_folders_are_removed_when_the_group_is_finished_and_when_posting_fails(monkeypatch):
    def folders():
        return {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("tg_media_mover_")}
    before = folders()
    client, _, state, messages = setup(monkeypatch, 3)
    pendings, _ = move_group(client, state, messages)
    assert all(not os.path.exists(p.workdir.path) for p in pendings) and folders() == before

    async def broken(client_, source, message, td, **kw):
        raise RuntimeError("no usable thumbnail")
    monkeypatch.setattr(tmm, "_select_manager_thumbnail_with_retry", broken)
    extra = client.add_source_video("scene x.mp4")
    with pytest.raises(RuntimeError):
        t.run(post_all(client, state, [extra]))
    assert folders() == before                                                              # a failed post leaves nothing behind


def test_each_video_gets_its_share_of_the_group_steps_in_the_timing(monkeypatch):
    client, _, state, messages = setup(monkeypatch, 4)
    move_group(client, state, messages)
    s = TIMER.summary()
    assert s["videos"] == 4 and s["failed"] == 0
    assert {"copy", "picture", "verify", "delete", "confirm"} <= set(s["steps"])


# ---------------------------------------------------------------- when a group is finished, and how big it may be ---------------------------------------
class P:
    def __init__(self, posted_at):
        self.posted_at = posted_at


def test_a_group_is_finished_when_full_when_a_pause_is_asked_for_or_when_the_oldest_has_waited_too_long():
    f = tmm.group_should_finish
    now = 1000.0
    assert f([], 20, paused=True, now=now) is False                                 # nothing waiting, nothing to do
    assert f([P(now)] * 19, 20, now=now) is False and f([P(now)] * 20, 20, now=now) is True
    assert f([P(now)], 20, paused=True, now=now) is True
    assert f([P(now - 59)], 20, now=now) is False and f([P(now - 61)], 20, now=now) is True
    assert f([P(now)], 1, now=now) is True                                           # size 1: every video is finished at once, as before


@pytest.mark.parametrize("detail,expected", [(None, 20), ({}, 20), ({"delivery_batch_size": 1}, 1), ({"delivery_batch_size": "8"}, 8),
                                              ({"delivery_batch_size": 0}, 1), ({"delivery_batch_size": 500}, 40), ({"delivery_batch_size": "many"}, 20),
                                              ({"delivery_batch_size": None}, 20)])
def test_the_group_size_comes_from_the_job_and_is_kept_in_range(detail, expected):
    assert tmm._delivery_batch_size(detail) == expected
