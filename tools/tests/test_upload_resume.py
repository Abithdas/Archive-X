import asyncio

from telegram_archive.engines import telegram_sender as ts


class FakeClient:
    """Acknowledges every part; can be told to fail one part on its first try."""
    def __init__(self, fail_part=None):
        self.fail_part = fail_part
        self.sent = []
    async def __call__(self, request):
        if request.file_part == self.fail_part:
            self.fail_part = None
            raise RuntimeError("connection reset")
        self.sent.append((request.file_id, request.file_part))
        return True


def _file(tmp_path, parts):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * (ts.UPLOAD_PART_BYTES * parts))
    return p


def test_failed_upload_resumes_from_confirmed_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "UPLOAD_PART_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(ts, "UPLOAD_MAX_WORKERS", 1)
    ts._UPLOAD_LEDGER.clear()
    path = _file(tmp_path, 4)
    client = FakeClient(fail_part=2)
    try:
        asyncio.run(ts._archive_x_upload_file(client, path))
        raise AssertionError("first attempt should fail")
    except RuntimeError:
        pass
    first_id = {i for i, _ in client.sent}.pop()
    assert sorted(p for _, p in client.sent) == [0, 1]
    notes = []
    result = asyncio.run(ts._archive_x_upload_file(client, path, status_callback=lambda m, s: notes.append(m)))
    assert sorted(p for _, p in client.sent) == [0, 1, 2, 3]      # parts 0 and 1 were not sent twice
    assert result.id == first_id and result.parts == 4            # same file id, so Telegram can join the parts
    assert notes == ["Resuming upload from part 3/4"]
    assert not ts._UPLOAD_LEDGER                                   # finished uploads are forgotten


def test_a_changed_file_never_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "UPLOAD_PART_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(ts, "UPLOAD_MAX_WORKERS", 1)
    ts._UPLOAD_LEDGER.clear()
    path = _file(tmp_path, 3)
    client = FakeClient(fail_part=1)
    try:
        asyncio.run(ts._archive_x_upload_file(client, path))
    except RuntimeError:
        pass
    path.write_bytes(b"y" * (ts.UPLOAD_PART_BYTES * 3 + 5))   # replaced on disk
    client.sent.clear()
    asyncio.run(ts._archive_x_upload_file(client, path))
    assert sorted(p for _, p in client.sent) == [0, 1, 2, 3]
