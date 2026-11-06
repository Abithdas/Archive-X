/* ============================================================================
 * FILE: js/40-responsive-player-layout.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Responsive player sizing only.
   Uses the selected video's known metadata before attaching the source, then
   confirms it from loaded media dimensions. Never changes source, position,
   play state, range scheduling, previews, or snapshot-grid state. */
(function () {
  'use strict';

  function selectedDimensions(video) {
    const hint = window.__axPlayerDimensionHint;
    // A selected-video hint always wins over videoWidth on the media element:
    // that element may still expose dimensions from the previous video while
    // the next item is being prepared.
    if (hint && hint.key) {
      if (Number(hint.width) > 0 && Number(hint.height) > 0) {
        return { width: Number(hint.width), height: Number(hint.height), known: true };
      }
      if (hint.waitingForMetadata) return { width: 16, height: 9, known: false };
    }
    if (video && video.readyState >= 1 && Number(video.videoWidth) > 0 && Number(video.videoHeight) > 0) {
      return { width: Number(video.videoWidth), height: Number(video.videoHeight), known: true };
    }
    return { width: 16, height: 9, known: false };
  }

  function syncAdaptivePlayerLayout() {
    const video = document.getElementById('pv');
    const visual = document.getElementById('pvisual');
    const player = document.getElementById('dtop');
    if (!visual || !player) return;

    const dims = selectedDimensions(video);
    const safeWidth = Number(dims.width) || 16;
    const safeHeight = Number(dims.height) || 9;
    const ratio = safeWidth / safeHeight;
    if (!Number.isFinite(ratio) || ratio <= 0) return;

    visual.dataset.orientation = ratio < 0.92 ? 'portrait' : (ratio <= 1.08 ? 'square' : 'landscape');
    visual.style.setProperty('--ax-player-aspect-ratio', `${safeWidth} / ${safeHeight}`);
    player.style.setProperty('--ax-player-aspect-ratio-num', String(ratio));

    // Bound the complete player to viewport height after reserving its controls.
    // Because the selected video's dimensions are supplied by openD/playB before
    // source attachment, the correct shape and control wrapping appear at once.
    const viewportHeight = window.innerHeight || 700;
    const targetPlayerHeight = Math.max(250, Math.min(viewportHeight * 0.76, viewportHeight - 140));
    const narrowViewport = (window.innerWidth || 900) <= 560;
    const orientation = visual.dataset.orientation;
    const controlAllowance = orientation === 'portrait'
      ? (narrowViewport ? 154 : 122)
      : (narrowViewport ? 112 : (orientation === 'square' ? 78 : 72));
    const maxVideoHeight = Math.max(160, targetPlayerHeight - controlAllowance);

    // Discover the actual player-column width where possible. #dmain may use
    // display:contents on large screens, so fall back to the #dm grid's first
    // track, then to the available viewport width.
    const dm = document.getElementById('dm');
    const dmWidth = Number(dm && dm.getBoundingClientRect().width) || (window.innerWidth || 900);
    let columnWidth = 0;
    if (dm) {
      const tracks = String(window.getComputedStyle(dm).gridTemplateColumns || '').match(/[\d.]+px/g) || [];
      if (tracks.length >= 2) columnWidth = Number.parseFloat(tracks[0]) || 0;
      else if (tracks.length === 1) columnWidth = Number.parseFloat(tracks[0]) || 0;
    }
    if (!(columnWidth > 0)) columnWidth = (window.innerWidth || dmWidth) <= 1100
      ? Math.max(200, dmWidth - 24)
      : Math.max(240, Math.min(dmWidth * 0.60, (window.innerWidth || dmWidth) - 360));
    columnWidth = Math.max(180, Math.min(columnWidth, (window.innerWidth || dmWidth) - 24));

    const ratioWidth = Math.floor(maxVideoHeight * ratio);
    const maxWidth = Math.max(180, Math.min(columnWidth, ratioWidth));
    player.style.setProperty('--ax-player-max-width', `${maxWidth}px`);
    player.dataset.sourceOrientation = orientation;
    player.dataset.dimensionsKnown = dims.known ? 'true' : 'false';
  }

  let queued = false;
  function scheduleSync() {
    if (queued) return;
    queued = true;
    const run = function () {
      queued = false;
      syncAdaptivePlayerLayout();
    };
    if (window.requestAnimationFrame) window.requestAnimationFrame(run);
    else window.setTimeout(run, 20);
  }

  // Public bridge used by the main app when a user selects a card, when info
  // metadata arrives, and when the active media confirms its intrinsic size.
  window.setArchiveXPlayerDimensionHint = function (hint) {
    window.__axPlayerDimensionHint = hint && typeof hint === 'object' ? {
      key: String(hint.key || ''),
      width: Number(hint.width) || 0,
      height: Number(hint.height) || 0,
      waitingForMetadata: !!hint.waitingForMetadata
    } : null;
    syncAdaptivePlayerLayout();
    if (typeof window.syncPlayerVisualState === 'function') window.syncPlayerVisualState();
  };
  window.syncAdaptivePlayerLayout = syncAdaptivePlayerLayout;

  function init() {
    scheduleSync();
    window.addEventListener('resize', scheduleSync, { passive: true });
    const video = document.getElementById('pv');
    if (video) {
      ['loadedmetadata', 'durationchange', 'resize'].forEach(eventName => {
        video.addEventListener(eventName, scheduleSync, { passive: true });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
