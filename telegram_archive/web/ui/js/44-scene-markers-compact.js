/* ============================================================================
 * FILE: js/44-scene-markers-compact.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Keeps the Scene Markers heading count in sync with rendered marker chips. */
(() => {
  'use strict';
  const boxId = 'v55-markers';
  const headId = 'dmarker-head';
  const headingBase = 'Scene markers';
  function syncMarkerCount() {
    const box = document.getElementById(boxId);
    const head = document.getElementById(headId);
    if (!box || !head) return;
    const count = box.querySelectorAll('.v55-marker').length;
    const nextText = count ? `${headingBase} (${count})` : headingBase;
    if (head.textContent.trim() !== nextText) head.textContent = nextText;
  }
  function init() {
    const box = document.getElementById(boxId);
    if (!box) return;
    syncMarkerCount();
    if (box.dataset.compactMarkerCountBound === '1') return;
    box.dataset.compactMarkerCountBound = '1';
    const observer = new MutationObserver(syncMarkerCount);
    observer.observe(box, { childList: true, subtree: true });
    document.addEventListener('archivex:details-updated', syncMarkerCount);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
