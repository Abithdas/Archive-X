"""A video in another data centre than the account's gets that connection opened ahead of its first range, on a task nothing cancels."""
import asyncio
from types import SimpleNamespace

from telegram_archive.playback.stream_server import PlaybackEngine


def _engine(base_dc, borrow, ret=None):
    async def _ret(sender):
        if ret is not None:
            ret.append(sender)
    client = SimpleNamespace(session=SimpleNamespace(dc_id=base_dc), _borrow_exported_sender=borrow, _return_exported_sender=_ret)
    return SimpleNamespace(client=client)


def _run(coro_factory):
    async def go():
        await coro_factory()
        await asyncio.sleep(0.05)
    asyncio.run(go())


def test_it_opens_the_other_data_centre_once_and_remembers_it():
    asked, returned = [], []

    async def borrow(dc):
        asked.append(dc)
        await asyncio.sleep(0.01)
        return f"sender{dc}"
    e = _engine(5, borrow, returned)

    async def go():
        PlaybackEngine._warm_dc_in_background(e, 4)
        PlaybackEngine._warm_dc_in_background(e, 4)           # already on its way: not asked twice
    _run(go)
    assert asked == [4] and returned == ["sender4"] and e._warm_dcs == {4} and not e._warming_dcs

    async def again():
        PlaybackEngine._warm_dc_in_background(e, 4)           # open now: nothing to do
    _run(again)
    assert asked == [4]


def test_the_account_s_own_data_centre_and_an_unknown_one_need_nothing():
    asked = []

    async def borrow(dc):
        asked.append(dc)
        return "s"
    e = _engine(5, borrow)

    async def go():
        PlaybackEngine._warm_dc_in_background(e, 5)
        PlaybackEngine._warm_dc_in_background(e, 0)
    _run(go)
    assert asked == []


def test_a_failed_attempt_is_not_remembered_as_open_and_can_be_tried_again():
    tries = []

    async def borrow(dc):
        tries.append(dc)
        raise ConnectionError("blocked")
    e = _engine(5, borrow)

    async def go():
        PlaybackEngine._warm_dc_in_background(e, 4)
    _run(go)
    assert tries == [4] and not getattr(e, "_warm_dcs", set()) and not e._warming_dcs

    async def retry():
        PlaybackEngine._warm_dc_in_background(e, 4)
    _run(retry)
    assert tries == [4, 4]
