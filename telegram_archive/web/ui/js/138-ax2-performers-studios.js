/* ============================================================================
 * FILE: js/138-ax2-performers-studios.js
 * AREA: Performers and studios
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* AX2 Step 4 — presentation-only count, image-load state and grid entrance hooks. */
(() => {
  'use strict';

  const byId = (id) => document.getElementById(id);

  function markLoaded(img) {
    if (!(img instanceof HTMLImageElement)) return;
    if (img.complete && img.naturalWidth > 0) img.classList.add('ax2-image-loaded');
  }

  function markGridImages(grid) {
    if (!grid) return;
    grid.querySelectorAll('img').forEach(markLoaded);
  }

  function syncStudioCount() {
    const list = byId('site-list');
    const count = byId('site-count');
    if (!list || !count) return;
    const visible = list.querySelectorAll(':scope > .pcard[data-entity-id]').length;
    const rawTotal = list.dataset.totalCount;
    const parsedTotal = Number(rawTotal);
    const total = rawTotal !== undefined && Number.isFinite(parsedTotal)
      ? Math.max(0, parsedTotal)
      : visible;
    count.textContent = total === visible
      ? `${total.toLocaleString()} ${total === 1 ? 'studio' : 'studios'}`
      : `${visible.toLocaleString()} shown · ${total.toLocaleString()} ${total === 1 ? 'studio' : 'studios'}`;
    count.setAttribute('data-count', String(visible));
    count.setAttribute('data-total-count', String(total));
  }

  function prepareGrid(grid, isStudioGrid = false) {
    if (!grid) return;
    grid.classList.add('ax2-entity-grid-motion');
    markGridImages(grid);
    if (isStudioGrid) syncStudioCount();
  }

  function init() {
    const performers = byId('perf-list');
    const studios = byId('site-list');
    prepareGrid(performers, false);
    prepareGrid(studios, true);

    const observerOptions = { childList: true, subtree: true };
    if (performers) {
      new MutationObserver(() => prepareGrid(performers, false)).observe(performers, observerOptions);
    }
    if (studios) {
      new MutationObserver(() => prepareGrid(studios, true)).observe(studios, observerOptions);
    }

    // load does not bubble; capture it so the class can be added without
    // replacing the app's existing inline load/error handlers.
    document.addEventListener('load', (event) => {
      const img = event.target;
      if (!(img instanceof HTMLImageElement)) return;
      if (img.closest('#perf-list, #site-list')) img.classList.add('ax2-image-loaded');
    }, true);

    document.addEventListener('error', (event) => {
      const img = event.target;
      if (!(img instanceof HTMLImageElement)) return;
      if (img.closest('#perf-list, #site-list')) img.classList.remove('ax2-image-loaded');
    }, true);

    // Studio counts remain synchronized with the current page and full result
    // total when the list is replaced or refreshed by the app runtime.
    syncStudioCount();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
