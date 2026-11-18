"""The Media Manager's console is also kept in logs/transfer-console.log, so a log zip shows what a transfer did."""
from telegram_archive import config
from telegram_archive.media_manager.runner import RUNNER


def test_console_lines_are_written_to_the_log_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    RUNNER._console_file = None
    RUNNER._commit("[SCAN COMPLETE] Messages: 10 | Video items: 4 | Selected: 0")
    RUNNER._commit("   ")                      # blank lines are not kept
    RUNNER._commit("  Skipping 4: no saved snapshots")
    RUNNER._console_file.close(); RUNNER._console_file = None
    text = (tmp_path / "transfer-console.log").read_text(encoding="utf-8")
    assert "Selected: 0" in text and "no saved snapshots" in text
    assert text.count("\n") == 2
