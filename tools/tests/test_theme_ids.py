"""The server only keeps theme ids it knows; a theme added to the web app must be added there too, or it resets to Nord on the next start."""
import re
from pathlib import Path

from telegram_archive.web import routes_experience

root = Path(__file__).resolve().parents[2]


def test_every_web_theme_id_is_accepted_by_the_server():
    web = set(re.findall(r"id: '([a-z-]+)'", (root / "apps/web/src/theme/palettes.ts").read_text(encoding="utf-8")))
    assert {"teal-coral", "neon-lime", "golden-noir", "nord"} <= web
    assert web <= routes_experience._AX_THEME_PRESETS
    system = (root / "telegram_archive/web/routes_system.py").read_text(encoding="utf-8")
    for theme in web:
        assert f'"{theme}"' in system, theme
