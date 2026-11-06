"""Canonical Telegram delivery profile shared by all Telegram delivery workflows.

The profile contains delivery policy only. Workflow-specific inputs such as a
selected source, automation RSS feed, or a direct-send target remain owned by
their respective workflows.
"""
from __future__ import annotations

from typing import Any

DELIVERY_PROFILE_VERSION = 1
PROFILE_TEMPLATE_KEY = "send_caption_template"
LEGACY_AUTOMATION_TEMPLATE_KEY = "content_automation_caption_template"

# Delivery policy is intentionally fixed by Archive X's canonical playback
# pipeline. These values are exposed as read-only state rather than duplicated
# per workflow.
PROFILE_DEFAULTS = {
    "cover_mode": "both",
    "snapshot_policy": "canonical-36-frame",
    "verification_policy": "video-and-representative-snapshot",
    "cleanup_policy": "only-after-verification",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _migrate_legacy_template(db, default_template: str) -> str:
    current = _clean(db.setting(PROFILE_TEMPLATE_KEY) or "")
    if current:
        return current
    legacy = _clean(db.setting(LEGACY_AUTOMATION_TEMPLATE_KEY) or "")
    if legacy:
        # The legacy automation template becomes the single shared template.
        # The sender sanitization is applied by the caller before persistence.
        db.set_setting(PROFILE_TEMPLATE_KEY, legacy)
        return legacy
    return default_template


def profile(db, *, default_template: str, sanitize_template) -> dict[str, Any]:
    template = sanitize_template(_migrate_legacy_template(db, default_template))
    return {
        "version": DELIVERY_PROFILE_VERSION,
        "template": template,
        "cover_mode": PROFILE_DEFAULTS["cover_mode"],
        "snapshot_policy": PROFILE_DEFAULTS["snapshot_policy"],
        "verification_policy": PROFILE_DEFAULTS["verification_policy"],
        "cleanup_policy": PROFILE_DEFAULTS["cleanup_policy"],
        "placeholders": ("title", "filename", "studio", "performers", "date", "duration", "resolution"),
        "source": "operations.send_to_telegram",
    }


def save_profile(db, data: dict[str, Any], *, default_template: str, sanitize_template) -> dict[str, Any]:
    if "template" in data:
        value = sanitize_template(_clean(data.get("template")) or default_template)
        db.set_setting(PROFILE_TEMPLATE_KEY, value)
        # Keep legacy automation storage synchronized only for old readers. New
        # workflows never read it; this prevents an older compatibility surface
        # from silently diverging from the canonical profile.
        db.set_setting(LEGACY_AUTOMATION_TEMPLATE_KEY, value)
    return profile(db, default_template=default_template, sanitize_template=sanitize_template)
