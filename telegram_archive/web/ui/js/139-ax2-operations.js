/* ============================================================================
 * FILE: js/139-ax2-operations.js
 * AREA: Operations and jobs
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X AX2 — Operations Downloads popover/sheet only. Existing actions stay unchanged. */
(() => {
  'use strict';

  const byId = (id) => document.getElementById(id);

  function closeDownloads(restoreFocus = false) {
    const menu = byId('ops-downloads-menu');
    const scrim = byId('ops-downloads-scrim');
    const toggle = byId('ops-downloads-toggle');
    if (!menu || !toggle) return;
    menu.hidden = true;
    if (scrim) scrim.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) toggle.focus({ preventScroll: true });
  }

  function openDownloads() {
    const menu = byId('ops-downloads-menu');
    const scrim = byId('ops-downloads-scrim');
    const toggle = byId('ops-downloads-toggle');
    if (!menu || !toggle) return;
    menu.hidden = false;
    if (scrim) scrim.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    const firstItem = menu.querySelector('[role="menuitem"]');
    if (firstItem) firstItem.focus({ preventScroll: true });
  }

  function toggleDownloads() {
    const menu = byId('ops-downloads-menu');
    if (!menu) return;
    if (menu.hidden) openDownloads();
    else closeDownloads();
  }

  // The markup's inline handlers call these public names; keep all existing
  // downloadSupportArtifact action handlers as-is.
  window.ax2ToggleOpsDownloads = toggleDownloads;
  window.ax2CloseOpsDownloads = () => closeDownloads();

  function init() {
    const menu = byId('ops-downloads-menu');
    const toggle = byId('ops-downloads-toggle');
    const logSection = byId('log-sec');
    if (!menu || !toggle) return;

    document.addEventListener('click', (event) => {
      if (menu.hidden) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (menu.contains(target)) {
        if (target.closest('[role="menuitem"]')) closeDownloads();
        return;
      }
      if (toggle.contains(target)) return;
      closeDownloads();
    });

    document.addEventListener('keydown', (event) => {
      if (menu.hidden) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        closeDownloads(true);
        return;
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        const items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
        if (!items.length) return;
        const active = items.indexOf(document.activeElement);
        const next = event.key === 'ArrowDown'
          ? (active + 1) % items.length
          : (active <= 0 ? items.length - 1 : active - 1);
        event.preventDefault();
        items[next].focus({ preventScroll: true });
      }
    });

    if (logSection) {
      logSection.addEventListener('toggle', () => {
        if (!logSection.open) closeDownloads();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
