"""SFW is not mingled with the adult databases: Edit video asks StashDB / ThePornDB for nothing and shows nothing from them."""
from test_screen_home import H, _client
from telegram_archive.core.database import Database


def test_in_sfw_the_provider_video_lookup_and_the_snapshot_suggestions_are_closed(tmp_path):
    db = Database(tmp_path / "t.db")
    c = _client(db)
    assert c.post("/api/media/quick-edit/lookup", json={"channel_id": "-1", "message_id": 1, "title": "x"}, headers=H).status_code == 409
    assert c.get("/api/media/snapshot-suggestions?channel_id=-1&message_id=1").json() == {"suggestions": []}
    assert c.post("/api/media/snapshot-suggestions/run", json={"channel_id": "-1", "message_id": 1}, headers=H).status_code == 409
    assert c.post("/api/media/snapshot-suggestions/reject", json={"channel_id": "-1", "message_id": 1, "kind": "performer"}, headers=H).status_code == 409
    assert c.get("/api/performers/provider-search?q=ava", headers=H).json() == []
    assert c.get("/api/studios/provider-search?q=big", headers=H).json() == []
