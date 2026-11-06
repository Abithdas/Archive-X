/* ============================================================================
 * FILE: js/102-sidebar-reference.js
 * AREA: Shell and navigation
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* v29.4.242 — Accessible sidebar collapse control; navigation targets stay unchanged. */
(function () {
  'use strict';
  const KEY = 'archiveX.sidebar.collapsed.v1';
  function initArchiveSidebarReference() {
    const root = document.getElementById('ax-sidebar');
    const toggle = document.getElementById('ax-sidebar-toggle');
    if (!root || !toggle || toggle.dataset.axSidebarReady === '1') return;
    toggle.dataset.axSidebarReady = '1';
    const glyph = toggle.querySelector('span');
    const apply = (collapsed, persist) => {
      document.body.classList.toggle('ax-sidebar-collapsed', !!collapsed);
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      toggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
      toggle.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
      if (glyph) glyph.textContent = collapsed ? '☰' : '×';
      if (persist) { try { localStorage.setItem(KEY, collapsed ? '1' : '0'); } catch (_) {} }
    };
    let collapsed = false;
    try { collapsed = localStorage.getItem(KEY) === '1'; } catch (_) {}
    apply(collapsed, false);
    toggle.addEventListener('click', () => apply(!document.body.classList.contains('ax-sidebar-collapsed'), true));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initArchiveSidebarReference, { once: true });
  else initArchiveSidebarReference();
})();
