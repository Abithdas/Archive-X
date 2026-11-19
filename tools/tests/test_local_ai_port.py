"""The assistant engine does not stop at a port that something else (qBittorrent uses 8080) already owns."""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import local_ai_runtime as lar


class FakeDb:
    def __init__(self):
        self.values = {}

    def setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


def test_a_busy_port_is_replaced_by_a_free_one_and_remembered():
    db = FakeDb()
    runtime = lar.LocalAIRuntime(db)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        db.values["ai_local_endpoint"] = f"http://127.0.0.1:{port}/v1"
        runtime._use_a_free_port()
        chosen = runtime._port()
    assert chosen != port and 1024 < chosen < 65536
    assert db.values["ai_local_endpoint"] == f"http://127.0.0.1:{chosen}/v1"


def test_a_free_port_is_left_as_it_is():
    db = FakeDb()
    runtime = lar.LocalAIRuntime(db)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    db.values["ai_local_endpoint"] = f"http://127.0.0.1:{free}/v1"
    runtime._use_a_free_port()
    assert db.values["ai_local_endpoint"] == f"http://127.0.0.1:{free}/v1"
