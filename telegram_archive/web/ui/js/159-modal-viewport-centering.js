/* ============================================================================
 * FILE: js/159-modal-viewport-centering.js
 * AREA: Dialogs and overlays
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Keep viewport-level dialogs independent from the scalable application canvas. */
(function () {
  'use strict';
  if (window.__axGlobalOverlayRootReady) return;
  window.__axGlobalOverlayRootReady = true;

  const ROOT_SELECTOR = [
    // Generic legacy modal class plus named viewport-level layers and drawers.
    '.mdl', '#ux-command', '#ux-activity', '#ux-filter-drawer',
    '#perf-duplicate-audit', '#channel-duplicate-audit', '#v54-job-detail',
    '#u-assets-scene-match-modal',
    '#channel-edit-modal', '#account-edit-modal', '#channel-shuffle-player',
    '#ax-confirm', '#pk', '#sk', '#psmap', '#video-quick-edit', '#vmr',
    '#entity-edit', '#amr', '#suggest-preview', '#sm', '#mm',
    '#video-move-dialog', '#ax-resume-dialog', '#toast', '#ax-exit-overlay',
    // Fixed sheets, scrim and account menu must also escape #ax-ui-scale-root.
    '#ax2-layer-scrim', '#ax2-avatar-menu', '#ax2-library-more-sheet', '#ax2-more-layer',
    // Created lazily by the portrait editor.
    '#ax-performer-portrait-adjust'
  ].join(',');

  let host = null;
  function getHost() {
    const scaleRoot = document.getElementById('ax-ui-scale-root');
    if (!host || !host.isConnected) host = document.getElementById('ax-global-overlay-root');
    if (!host) {
      host = document.createElement('div');
      host.id = 'ax-global-overlay-root';
      host.setAttribute('data-ax-global-overlay-root', '');
      host.setAttribute('aria-live', 'off');
    }
    // If an earlier build or a dynamically-created host left this inside the
    // CSS-zoomed canvas, reparent it to body before attaching any overlays.
    if (document.body && (host.parentElement !== document.body || (scaleRoot && scaleRoot.contains(host)))) {
      document.body.appendChild(host);
    }
    return host;
  }

  function portalRoots() {
    if (!document.body) return;
    const destination = getHost();
    // Query in current document order so equal-z-index layers keep their expected stacking order.
    document.querySelectorAll(ROOT_SELECTOR).forEach(element => {
      // While a modal <dialog> is open the toast rides inside it (js/143) so it stays on top of the
      // dialog's backdrop and clickable; do not pull it back out.
      if (element.id === 'toast' && element.closest('dialog')) return;
      if (element !== destination && element.parentElement !== destination) {
        destination.appendChild(element);
      }
    });
  }

  // Move existing static roots now; IDs and inline handlers remain unchanged.
  portalRoots();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', portalRoots, { once: true });
  }

  // Exit UI is created dynamically during shutdown. Rehome only known overlay roots.
  const observer = new MutationObserver(records => {
    // Only react when an added node is (or contains) an overlay root. Re-scanning the whole
    // document for every DOM insertion made every list/card render and live-status update pay for it.
    const host = document.getElementById('ax-global-overlay-root');
    let hit = false;
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1 || node === host || (host && node.parentElement === host)) continue;
        if (node.id === 'toast' && node.closest('dialog')) continue;
        if (node.matches(ROOT_SELECTOR) || node.querySelector(ROOT_SELECTOR)) { hit = true; break; }
      }
      if (hit) break;
    }
    if (hit) portalRoots();
  });
  if (document.body) observer.observe(document.body, { childList: true, subtree: true });
  window.addEventListener('pageshow', portalRoots, { once: true });
})();
