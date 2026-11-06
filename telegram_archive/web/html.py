"""Compose the Archive X dashboard from web/ui/ partials."""
from __future__ import annotations
import re
from pathlib import Path

UI_DIR = Path(__file__).with_name("ui")
_INCLUDE = re.compile(r"<!--#include ([A-Za-z0-9_./-]+)-->")
_HTML_PARTIAL_INCLUDE = re.compile(r"<!--#include (html/[A-Za-z0-9_./-]+)-->")
_LAYER_SCRIPT_SLOT = "<!--#layers-script-slot-->"
# Late layers are appended after the legacy cascade to keep the HTML entry point and the
# playback runtime isolated from presentation-only improvements.
_LAYERS = [
    ("style", "archive-x-nobuf-frontend-redesign", "css/30-nobuf-frontend-redesign.css"),
    ("style", "archive-x-nobuf-settings-rhythm", "css/32-nobuf-settings-rhythm.css"),
    ("style", "archive-x-settings-navigation", "css/33-settings-navigation.css"),
    ("style", "archive-x-settings-visual-polish", "css/34-settings-visual-polish.css"),
    ("script", "archive-x-settings-navigation-runtime", "js/33-settings-navigation.js"),
    ("style", "archive-x-custom-theme-system-style", "css/35-custom-theme-system.css"),
    ("script", "archive-x-custom-theme-system-runtime", "js/35-custom-theme-system.js"),
    ("style", "archive-x-playback-settings-controls", "css/36-playback-settings-controls.css"),
    ("style", "archive-x-player-page-reference-layout", "css/37-player-page-reference-layout.css"),
    ("style", "archive-x-player-page-dropdown-polish", "css/38-player-page-dropdown-polish.css"),
    ("script", "archive-x-autonext-custom-dropdown-runtime", "js/38-autonext-custom-dropdown.js"),
    ("style", "archive-x-global-select-checklist-controls", "css/39-global-select-checklist-controls.css"),
    ("script", "archive-x-global-select-checklist-controls-runtime", "js/39-global-select-checklist-controls.js"),
    ("style", "archive-x-responsive-player-layout", "css/40-responsive-player-layout.css"),
    ("script", "archive-x-responsive-player-layout-runtime", "js/40-responsive-player-layout.js"),
    ("style", "archive-x-watch-page-layout-and-seek-preview", "css/41-watch-page-layout-and-seek-preview.css"),
    ("style", "archive-x-watch-header-metadata-style", "css/42-watch-header-metadata.css"),
    ("script", "archive-x-watch-header-metadata-runtime", "js/42-watch-header-metadata.js"),
    ("style", "archive-x-recommendation-carousels-style", "css/43-recommendation-carousels.css"),
    ("script", "archive-x-recommendation-carousels-runtime", "js/43-recommendation-carousels.js"),
    ("style", "archive-x-scene-markers-compact-style", "css/44-scene-markers-compact.css"),
    ("script", "archive-x-scene-markers-compact-runtime", "js/44-scene-markers-compact.js"),
    ("style", "archive-x-netflix-hover-cards-style", "css/45-netflix-hover-cards.css"),
    ("script", "archive-x-netflix-hover-cards-runtime", "js/45-netflix-hover-cards.js"),
    ("style", "archive-x-netflix-navigation-arrows", "css/46-netflix-navigation-arrows.css"),
    ("style", "archive-x-telegram-login-experience-style", "css/47-telegram-login-experience.css"),
    ("script", "archive-x-telegram-login-experience-runtime", "js/47-telegram-login-experience.js"),
    ("style", "archive-x-operations-dashboard-redesign", "css/48-operations-dashboard-redesign.css"),
    ("style", "archive-x-transfer-workspace", "css/49-transfer-workspace.css"),
    ("style", "archive-x-operations-after-transfer-split", "css/50-operations-after-transfer-split.css"),
    ("style", "archive-x-video-page-minimize", "css/51-video-page-minimize.css"),
    ("script", "archive-x-video-page-minimize-runtime", "js/51-video-page-minimize.js"),
    ("style", "archive-x-compact-header-search", "css/52-header-search-compact.css"),
    ("style", "archive-x-glass-player-controls", "css/53-glass-player-controls.css"),
    ("style", "archive-x-player-clarity-live-preview", "css/54-player-clarity-live-preview.css"),
    ("style", "archive-x-video-page-cohesion-restore", "css/55-video-page-cohesion.css"),
    ("style", "archive-x-player-audio-skip-controls", "css/56-player-audio-skip-controls.css"),
    ("style", "archive-x-adaptive-watch-page-stability", "css/57-adaptive-watch-page-stability.css"),
    ("style", "archive-x-live-seek-preview-module-style", "css/60-live-seek-preview-module.css"),
    ("style", "archive-x-snapshot-grid-module-style", "css/61-snapshot-grid-module.css"),
    ("style", "archive-x-upnext-arrows-related-video-entities", "css/62-upnext-arrows-related-video-entities.css"),
    ("style", "archive-x-video-related-entity-editor", "css/63-video-related-entity-editor.css"),
    ("style", "archive-x-entity-marker-chip-refinement", "css/64-entity-marker-chip-refinement.css"),
    ("style", "archive-x-performer-directory-reference-layout-style", "css/65-performer-directory-reference-layout.css"),
    ("style", "archive-x-performer-profile-overview-layout", "css/66-performer-profile-overview-layout.css"),
    ("style", "archive-x-studio-square-directory-cards", "css/67-studio-square-directory-cards.css"),
    ("style", "archive-x-video-card-reference-layout", "css/68-video-card-reference-layout.css"),
    ("style", "archive-x-video-card-performer-tag-differentiation", "css/69-video-card-performer-tag-differentiation.css"),
    ("style", "archive-x-video-card-spacious-layout", "css/70-video-card-spacious-layout.css"),
    ("style", "archive-x-library-pagination-reference", "css/71-library-pagination-reference.css"),
    ("style", "archive-x-studio-artwork-zone-before-title", "css/72-studio-artwork-zone-before-title.css"),
    ("style", "archive-x-performer-profile-enhancements-style", "css/73-performer-profile-enhancements.css"),
    ("script", "archive-x-performer-profile-enhancements-runtime", "js/73-performer-profile-enhancements.js"),
    ("style", "archive-x-performer-detail-header-layout-fix", "css/74-performer-detail-header-layout-fix.css"),
    ("style", "archive-x-loading-feedback-patterns-style", "css/75-loading-feedback-patterns.css"),
    ("script", "archive-x-loading-feedback-patterns-runtime", "js/75-loading-feedback-patterns.js"),
    ("style", "archive-x-transfer-workspace-compact", "css/76-transfer-workspace-compact.css"),
    ("style", "archive-x-integrated-adaptive-player-shell", "css/77-integrated-adaptive-player-shell.css"),
    ("style", "archive-x-global-video-card-hover-pop", "css/78-global-video-card-hover-pop.css"),
    ("style", "archive-x-video-card-centered-badges", "css/79-video-card-centered-badges.css"),
    ("style", "archive-x-video-card-avatar-border-alignment", "css/80-video-card-avatar-border-alignment.css"),
    ("style", "archive-x-sidebar-storage-summary", "css/81-sidebar-storage-summary.css"),
    ("style", "archive-x-metadata-job-completion-clarity", "css/82-metadata-job-completion-clarity.css"),
    ("style", "archive-x-video-quick-edit-provider-search-redesign", "css/83-video-quick-edit-provider-search-redesign.css"),
    ("style", "archive-x-video-quick-edit-viewport-containment", "css/84-video-quick-edit-viewport-containment.css"),
    ("style", "archive-x-video-card-performer-chip-box-sizing", "css/85-video-card-performer-chip-box-sizing.css"),
    ("style", "archive-x-similar-performer-match-scoring", "css/86-similar-performer-match-scoring.css"),
    ("style", "archive-x-similar-performer-main-card-style", "css/87-similar-performer-main-card-style.css"),
    ("style", "archive-x-smart-video-tag-taxonomy", "css/88-smart-video-tag-taxonomy.css"),
    ("style", "archive-x-performer-related-cards-main-style-no-edit", "css/89-performer-related-cards-main-style-no-edit.css"),
    ("style", "archive-x-studio-profile-overview-layout", "css/91-studio-profile-overview-layout.css"),
    ("style", "archive-x-studio-related-cards-match-performer", "css/92-studio-related-cards-match-performer.css"),
    ("style", "archive-x-performer-toolbar-reference-style", "css/93-performer-toolbar-reference-style.css"),
    ("style", "archive-x-related-performer-card-gradient-fix", "css/94-related-performer-card-gradient-fix.css"),
    ("style", "archive-x-transfer-manager-ui-refinement", "css/95-transfer-manager-ui-refinement.css"),
    ("style", "archive-x-metadata-progress-compact", "css/96-metadata-progress-compact.css"),
    ("style", "archive-x-content-automation-layout-fix", "css/98-content-automation-layout-fix.css"),
    ("style", "archive-x-player-controls-below-video-visible", "css/99-player-controls-below-video-visible.css"),
    ("style", "archive-x-transfer-manager-layout-polish", "css/100-transfer-manager-layout-polish.css"),
    ("style", "archive-x-edit-icon-reference-style", "css/101-edit-icon-reference-style.css"),
    ("style", "archive-x-sidebar-reference-style", "css/102-sidebar-reference-style.css"),
    ("script", "archive-x-sidebar-reference-runtime", "js/102-sidebar-reference.js"),
    ("style", "archive-x-channel-video-file-cards", "css/103-channel-video-file-cards.css"),
    ("style", "archive-x-channel-card-theme-cohesion", "css/104-channel-card-theme-cohesion.css"),
    ("style", "archive-x-exit-app-control", "css/105-exit-app-control.css"),
    ("script", "archive-x-exit-app-runtime", "js/105-exit-app.js"),
    ("style", "archive-x-library-readiness-ui-cleanup", "css/106-library-readiness-ui-cleanup.css"),
    ("style", "archive-x-transfer-page-alignment-fix", "css/107-transfer-page-alignment-fix.css"),
    ("style", "archive-x-compact-library-layout", "css/108-compact-library-layout.css"),
    ("style", "archive-x-transfer-metadata-wizards", "css/109-transfer-metadata-wizards.css"),
    ("script", "archive-x-transfer-metadata-wizards-runtime", "js/109-transfer-metadata-wizards.js"),
    ("style", "archive-x-telegram-profile-channel-cards", "css/110-telegram-profile-channel-cards.css"),
    ("style", "archive-x-brown-video-card-layout", "css/111-brown-video-card-layout.css"),
    ("style", "archive-x-transfer-density-compact", "css/112-transfer-density-compact.css"),
    ("style", "archive-x-compact-operations-settings", "css/113-compact-operations-settings.css"),
    ("style", "archive-x-theme-engine-enhancements", "css/114-theme-engine-enhancements.css"),
    ("script", "archive-x-theme-engine-enhancements-runtime", "js/114-theme-engine-enhancements.js"),
    ("style", "archive-x-channel-profile-card-refinements", "css/115-channel-profile-card-refinements.css"),
    ("style", "archive-x-app-wide-theme-density", "css/116-app-wide-theme-density.css"),
    ("style", "archive-x-transfer-resource-chip-alignment", "css/118-transfer-resource-chip-alignment.css"),
    ("style", "archive-x-video-card-fixed-track-layout", "css/119-video-card-fixed-track-layout.css"),
    ("style", "archive-x-channel-card-centering-and-compact-size", "css/117-channel-card-centering-compact.css"),
    ("style", "archive-x-channel-card-true-centering", "css/120-channel-card-true-centering.css"),
    ("style", "archive-x-transfer-workspace-full-width", "css/121-transfer-workspace-full-width.css"),
    ("style", "archive-x-bulk-edit-toolbar-cohesion", "css/122-bulk-edit-toolbar-cohesion.css"),
    ("style", "archive-x-library-player-button-clarity", "css/123-library-player-button-clarity.css"),
    ("style", "archive-x-centered-compact-blurred-dialogs", "css/124-centered-compact-blurred-dialogs.css"),
    ("style", "archive-x-channel-duplicate-maintenance", "css/125-channel-duplicate-maintenance.css"),
    ("style", "archive-x-entity-profile-picture-dialog", "css/126-entity-profile-picture-dialog.css"),
    ("style", "archive-x-player-overlay-controls-autohide", "css/127-player-overlay-controls-autohide.css"),
    ("style", "archive-x-operations-channel-spacing-fullscreen-controls", "css/128-operations-channel-spacing-and-fullscreen-controls.css"),
    ("style", "archive-x-player-scene-chapters", "css/129-player-scene-chapters.css"),
    ("style", "archive-x-cross-input-parity", "css/130-cross-input-parity.css"),
    ("script", "archive-x-cross-input-parity-runtime", "js/130-cross-input-parity.js"),
    ("style", "archive-x-iphone-13-mini-mobile-first", "css/131-iphone-13-mini-mobile-first.css"),
    ("style", "archive-x-responsive-system", "css/132-responsive-system.css"),
    ("style", "archive-x-design-system", "css/133-design-system.css"),
    ("style", "archive-x-ax2-foundation", "css/134-ax2-foundation.css"),
    ("style", "archive-x-ax2-shell", "css/135-ax2-shell.css"),
    ("script", "archive-x-ax2-shell-runtime", "js/135-ax2-shell.js"),
    ("style", "archive-x-ax2-videos", "css/136-ax2-videos.css"),
    ("script", "archive-x-ax2-videos-runtime", "js/136-ax2-videos.js"),
    ("style", "archive-x-ax2-video-page", "css/137-ax2-video-page.css"),
    ("script", "archive-x-ax2-video-page-runtime", "js/137-ax2-video-page.js"),
    ("style", "archive-x-ax2-performers-studios", "css/138-ax2-performers-studios.css"),
    ("script", "archive-x-ax2-performers-studios-runtime", "js/138-ax2-performers-studios.js"),
    ("style", "archive-x-ax2-operations", "css/139-ax2-operations.css"),
    ("script", "archive-x-ax2-operations-runtime", "js/139-ax2-operations.js"),
    ("style", "archive-x-ax2-transfers", "css/140-ax2-transfers.css"),
    ("script", "archive-x-ax2-transfers-runtime", "js/140-ax2-transfers.js"),
    ("style", "archive-x-ax2-settings-ai-signin", "css/141-ax2-settings-ai-signin.css"),
    ("script", "archive-x-ax2-settings-runtime", "js/141-ax2-settings.js"),
    ("style", "archive-x-ax2-page-parity", "css/142-ax2-page-parity.css"),
    ("script", "archive-x-ax2-view-layout-parity", "js/142-ax2-view-layout-parity.js"),
    ("style", "archive-x-ax2-real-device-fixes", "css/142-ax2-fixes.css"),
    ("style", "archive-x-ax2-idempotence-hotfix", "css/144-ax2-idempotence-fixes.css"),
    ("style", "archive-x-final-parity", "css/145-ax-final-parity.css"),
    ("script", "archive-x-final-parity-runtime", "js/145-ax-final-parity.js"),
    ("style", "archive-x-reference-hover-video-page", "css/146-reference-hover-video-page.css"),
    ("style", "archive-x-reference-ui-parity", "css/147-reference-ui-parity.css"),
    ("style", "archive-x-reference-exact", "css/148-reference-exact.css"),
    ("style", "archive-x-placeholder", "css/149-ax-placeholder.css"),
    ("style", "archive-x-transfer-fixed-size", "css/150-ax-transfer-fixed-size.css"),
    ("style", "archive-x-settings-parity", "css/151-ax-settings-parity.css"),
    ("style", "archive-x-operations-parity", "css/152-ax-operations-parity.css"),
    ("style", "archive-x-player-polish", "css/153-ax-player-polish.css"),
    ("script", "archive-x-account-profile", "js/154-ax-account-profile.js"),
    ("script", "archive-x-reference-exact-runtime", "js/148-reference-exact.js"),
    ("script", "archive-x-settings-parity-runtime", "js/151-ax-settings-parity.js"),
    ("script", "archive-x-operations-parity-runtime", "js/152-ax-operations-parity.js"),
    ("style", "archive-x-ax2-detail-pages-dialogs", "css/143-ax2-details-dialogs.css"),
    ("script", "archive-x-ax2-detail-pages-dialogs-runtime", "js/143-ax2-details-dialogs.js"),
    ("style", "archive-x-operations-maintenance-tools", "css/155-operations-maintenance-tools.css"),
    ("style", "archive-x-assets-run-actions", "css/156-assets-run-actions.css"),
    ("style", "archive-x-operations-scene-match-process", "css/157-operations-scene-match-process.css"),
    ("style", "archive-x-transfer-live-lane-controls", "css/158-transfer-live-lane-controls.css"),
    ("style", "archive-x-modal-viewport-centering", "css/159-modal-viewport-centering.css"),
    ("script", "archive-x-modal-viewport-centering-runtime", "js/159-modal-viewport-centering.js"),
    ("style", "archive-x-video-quick-edit-reference", "css/160-video-quick-edit-reference.css"),
    ("style", "archive-x-ui-regression-hardening", "css/161-ui-regression-hardening.css"),
    ("script", "archive-x-ui-regression-hardening-runtime", "js/161-ui-regression-hardening.js"),
    ("style", "archive-x-reference-fidelity-final", "css/162-reference-fidelity-final.css"),
    ("script", "archive-x-reference-fidelity-final-runtime", "js/162-reference-fidelity-final.js"),
    ("style", "archive-x-profile-channel-edit-reference", "css/163-profile-channel-edit-reference.css"),
    ("style", "archive-x-player-error-overlay", "css/164-player-error-overlay.css"),
    ("style", "archive-x-video-player-reference", "css/165-video-player-reference.css"),
    ("style", "archive-x-connection-state-banner", "css/166-connection-state-banner.css"),
    ("style", "archive-x-smooth-motion-system", "css/97-smooth-motion-system.css"),
    ("style", "archive-x-sidebar-content-gutter", "css/167-sidebar-content-gutter.css"),
    ("style", "archive-x-sidebar-logo-spacing", "css/168-sidebar-logo-spacing.css"),
    ("script", "archive-x-smooth-motion-runtime", "js/97-smooth-motion-system.js"),
    ("style", "archive-x-player-ab-repeat", "css/169-player-ab-repeat.css"),
    ("style", "archive-x-channel-shuffle-reference", "css/170-channel-shuffle-reference.css"),
    ("style", "archive-x-nobuf-buffer-cache-style", "css/171-nobuf-buffer-cache.css"),
    ("style", "archive-x-seek-idle-style", "css/172-ax-seek-idle-style.css"),
    ("style", "archive-x-nobuf-theme", "css/173-ax-nobuf-theme.css"),
    ("style", "archive-x-smooth-snapshot-grid-play-button", "css/174-snapshot-grid-smooth-play.css"),
    ("script", "archive-x-nobuf-buffer-cache-runtime", "js/171-nobuf-buffer-cache.js"),
    # Keep canonical component styles before the final audit/regression overrides.
    ("style", "archive-x-component-nav", "css/components/nav.css"),
    ("style", "archive-x-component-player", "css/components/player.css"),
    ("style", "archive-x-component-cards", "css/components/cards.css"),
    ("style", "archive-x-ux-audit", "css/175-ux-audit.css"),
    ("script", "archive-x-ux-audit-runtime", "js/175-ux-audit.js"),
    ("style", "archive-x-account-profile-modal-stability", "css/176-account-profile-modal-stability.css"),
    ("style", "archive-x-profile-card-library-parity", "css/177-profile-card-library-parity.css"),
    ("style", "archive-x-photo-library-style", "css/180-photo-library.css"),
    ("script", "archive-x-photo-library-runtime", "js/180-photo-library.js"),
    ("script", "archive-x-photo-actions-runtime", "js/181-photo-actions.js"),
    ("style", "archive-x-transfer-image-upload-mode", "css/181-transfer-image-upload-mode.css"),
    ("style", "archive-x-video-sort-dropdown", "css/182-video-sort-dropdown.css"),
    ("style", "archive-x-photo-library-layout-parity", "css/183-photo-library-layout-parity.css"),
    ("style", "archive-x-responsive-hardening", "css/184-responsive-hardening.css"),
    ("style", "archive-x-mobile-app", "css/190-mobile-app.css"),
    ("script", "archive-x-mobile-app-runtime", "js/190-mobile-app.js"),
    ("style", "archive-x-responsive-fluid", "css/191-responsive-fluid.css"),
    ("style", "archive-x-shimmer-skeleton", "css/192-shimmer-skeleton.css"),
    ("script", "archive-x-shimmer-skeleton-runtime", "js/192-shimmer-skeleton.js"),
]


def _asset_path(rel: str) -> Path:
    """Resolve a bundled UI asset and reject paths outside web/ui/css or web/ui/js."""
    path = (UI_DIR / rel).resolve()
    allowed_roots = ((UI_DIR / "css").resolve(), (UI_DIR / "js").resolve())
    if not any(root == path or root in path.parents for root in allowed_roots):
        raise ValueError(f"UI asset outside css/js: {rel}")
    return path


def _read(rel: str) -> str:
    path = (UI_DIR / rel).resolve()
    if UI_DIR.resolve() not in path.parents:
        raise ValueError(f"UI include outside ui/: {rel}")
    return path.read_bytes().decode("utf-8-sig")


def _expand_html_partials(page: str, stack: tuple[str, ...] = ()) -> str:
    """Expand nested HTML view partials while leaving CSS/JS asset includes intact."""
    def replace(match: re.Match[str]) -> str:
        rel = match.group(1)
        if rel in stack:
            chain = " -> ".join((*stack, rel))
            raise ValueError(f"Circular HTML partial include: {chain}")
        partial = UI_DIR / rel
        if not partial.is_file():
            raise FileNotFoundError(f"Archive X HTML partial missing: {rel}")
        return _expand_html_partials(_read(rel), (*stack, rel))

    return _HTML_PARTIAL_INCLUDE.sub(replace, page)


def asset_url(rel: str) -> str:
    """Return a content-fingerprinted URL so browsers can cache CSS/JS safely."""
    import hashlib
    from urllib.parse import quote

    path = _asset_path(rel)
    if not path.is_file():
        raise FileNotFoundError(f"Archive X UI asset missing: {rel}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"/ui-assets/{quote(rel, safe='/.-_')}?v={digest}"


_INLINE_STYLE_INCLUDE = re.compile(
    r'<style(?P<attrs>[^>]*)>\s*<!--#include (?P<rel>css/[A-Za-z0-9_./-]+)-->\s*</style>',
    re.IGNORECASE,
)
_INLINE_SCRIPT_INCLUDE = re.compile(
    r'<script(?P<attrs>[^>]*)>\s*<!--#include (?P<rel>js/[A-Za-z0-9_./-]+)-->\s*</script>',
    re.IGNORECASE,
)


def _source_css_order(page: str) -> list[str]:
    """Return CSS includes in markup order, followed by the explicit late layers."""
    page = _expand_html_partials(page)
    ordered = [match.group("rel") for match in _INLINE_STYLE_INCLUDE.finditer(page)]
    ordered.extend(rel for tag, _element_id, rel in _LAYERS if tag == "style")
    return ordered


def _build_css_bundle() -> bytes:
    """Bundle the existing modular styles without changing their cascade order.

    The source CSS files remain separate and editable. The browser receives one
    fingerprinted stylesheet, avoiding a request waterfall across 170 CSS files.
    """
    page = _read("index.html")
    refs = _source_css_order(page)
    missing = [rel for rel in refs if not (UI_DIR / rel).is_file()]
    if missing:
        raise FileNotFoundError(f"Archive X CSS files missing: {', '.join(missing)}")
    chunks = []
    for rel in refs:
        chunks.append(f"\n/* ===== Archive X source: {rel} ===== */\n")
        chunks.append(_read(rel))
        chunks.append("\n")
    return "".join(chunks).encode("utf-8")


CSS_BUNDLE_BYTES = _build_css_bundle()
CSS_BUNDLE_SHA256 = __import__("hashlib").sha256(CSS_BUNDLE_BYTES).hexdigest()
CSS_BUNDLE_VERSION = CSS_BUNDLE_SHA256[:16]
CSS_BUNDLE_URL = f"/ui-assets/app.css?v={CSS_BUNDLE_VERSION}"


def _tag_id(attrs: str) -> str:
    match = re.search(r'\bid=(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', attrs, re.IGNORECASE)
    if not match:
        return ""
    value = next((item for item in match.groups() if item is not None), "")
    return f' id="{value}"' if re.fullmatch(r'[A-Za-z0-9_:-]+', value) else ""


def build_page() -> str:
    """Compose the final HTML shell: expand partials, then link the CSS and JS."""
    # Keep the entry shell readable: expand reusable view/modal partials before
    # collecting assets so DOM order and the original CSS cascade stay stable.
    page = _expand_html_partials(_read("index.html"))
    refs = _INCLUDE.findall(page)
    missing = [p for p in refs if not (UI_DIR / p).is_file()]
    missing += [rel for _, _, rel in _LAYERS if not (UI_DIR / rel).is_file()]
    if missing:
        raise FileNotFoundError(f"Archive X UI files missing: {', '.join(missing)} (copy the whole web/ui folder)")

    # Serve CSS as one content-fingerprinted bundle while preserving the source
    # file order exactly. The editable CSS files remain modular in web/ui/css.
    # Keep the theme bootstrap inline so it can set the theme before first paint.
    stylesheet_inserted = False

    def replace_style(match: re.Match[str]) -> str:
        nonlocal stylesheet_inserted
        if stylesheet_inserted:
            return ""
        stylesheet_inserted = True
        return f'<link rel="stylesheet" id="archive-x-app-styles" href="{CSS_BUNDLE_URL}">'

    def replace_script(match: re.Match[str]) -> str:
        rel = match.group("rel")
        if rel == "js/02-theme-bootstrap.js":
            return match.group(0).replace(match.group(0), f'<script{match.group("attrs")}>{_read(rel)}</script>')
        return f'<script{_tag_id(match.group("attrs"))} src="{asset_url(rel)}"></script>'

    page = _INLINE_STYLE_INCLUDE.sub(replace_style, page)
    page = _INLINE_SCRIPT_INCLUDE.sub(replace_script, page)
    assembled = _INCLUDE.sub(lambda m: _read(m.group(1)), page)

    # CSS layers are already included in app.css. Preserve the ordered classic
    # script tags separately: legacy inline handlers and globals rely on their
    # current execution order, so do not concatenate or async-load JavaScript.
    layers = []
    for tag, element_id, rel in _LAYERS:
        if tag != "script":
            continue
        layers.append(f'<script id="{element_id}" src="{asset_url(rel)}"></script>\n')
    layers = "\n" + "".join(layers)
    # Place the later runtime modules at an explicit template slot. This keeps
    # their established execution order and avoids emitting JavaScript after </html>.
    if assembled.count(_LAYER_SCRIPT_SLOT) != 1:
        raise ValueError("Frontend template must contain exactly one layer-script slot")
    return assembled.replace(_LAYER_SCRIPT_SLOT, layers, 1)

HTML = build_page()

# The page is a constant HTML app shell; CSS/JS are served as versioned assets
# and user data arrives through the authenticated API. Compress the shell once
# and let browsers revalidate with an ETag instead of resending it on each load.
import gzip as _gzip
import hashlib as _hashlib

HTML_BYTES = HTML.encode("utf-8")
HTML_GZIP = _gzip.compress(HTML_BYTES, compresslevel=9, mtime=0)
HTML_ETAG = '"axui-' + _hashlib.sha256(HTML_BYTES).hexdigest()[:24] + '"'


def page_response(request):
    """Serve the app shell: gzip when accepted, 304 when the browser copy is current."""
    from starlette.responses import Response
    headers = {"Cache-Control": "no-cache", "ETag": HTML_ETAG, "Vary": "Accept-Encoding"}
    inm = request.headers.get("if-none-match", "")
    if inm and HTML_ETAG in [x.strip() for x in inm.split(",")]:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(HTML_GZIP, media_type="text/html; charset=utf-8",
                        headers={**headers, "Content-Encoding": "gzip"})
    return Response(HTML_BYTES, media_type="text/html; charset=utf-8", headers=headers)
