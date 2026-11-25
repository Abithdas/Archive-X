"""Starting the application must not import the Telegram library (about 0.4 s); it is loaded when the first Telegram client is made."""
import subprocess
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])


def run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip()


def test_importing_the_application_does_not_load_telegram():
    assert run("import sys; import telegram_archive.app; print('telethon' in sys.modules)") == "False"


def test_the_governed_client_loads_it_when_the_first_client_is_made():
    code = (
        "import sys; import telegram_archive.app\n"
        "from telegram_archive.telegram.client_policy import make_telegram_client\n"
        "assert 'telethon' not in sys.modules\n"
        "from telethon.sessions import MemorySession\n"
        "c = make_telegram_client(MemorySession(), 12345, 'a' * 32)\n"
        "from telegram_archive.telegram.governed_client import GovernedTelegramClient\n"
        "print(type(c).__name__, isinstance(c, GovernedTelegramClient), 'telethon' in sys.modules)"
    )
    assert run(code) == "GovernedTelegramClient True True"


def test_the_lazy_helpers_give_the_real_classes_and_loads_nothing_until_asked():
    code = (
        "import sys\n"
        "from telegram_archive.core import telethon_lazy as t\n"
        "assert t.available() and 'telethon' not in sys.modules\n"
        "from telethon import errors\n"
        "print(t.errors().FloodWaitError is errors.FloodWaitError, t.functions().__name__, t.types().__name__, t.memory_session() is not None, t.document_attribute_filename().__name__)"
    )
    assert run(code) == "True telethon.functions telethon.types True DocumentAttributeFilename"


def test_without_the_library_the_helpers_fall_back_like_the_old_imports_did():
    code = (
        "import sys; sys.modules['telethon'] = None\n"
        "from telegram_archive.core import telethon_lazy as t\n"
        "e = t.errors()\n"
        "print(t.functions(), t.types(), t.memory_session(), t.document_attribute_filename(), issubclass(e.FloodWaitError, Exception), e.FloodWaitError is e.SessionPasswordNeededError)"
    )
    assert run(code) == "None None None None True True"
