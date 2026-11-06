/* ============================================================================
 * FILE: js/162-reference-fidelity-final.js
 * AREA: Design system and global polish
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* v162 — focus trap + Escape for legacy .mdl dialogs not covered by their own handlers. */
(() => {
  if (window.__axRf162) return; window.__axRf162 = true;
  const SEL = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  const topModal = () => { const l = [...document.querySelectorAll('.mdl.open')]; return l[l.length - 1] || null; };
  const vis = el => el.offsetParent !== null || el.getClientRects().length;
  let last = null;
  document.addEventListener('keydown', ev => {
    const m = topModal(); if (!m) return;
    if (window.ax2 && document.querySelector('.ax2-menu:not([hidden]).open,.ax2-sheet:not([hidden]).open')) return;
    if (ev.key === 'Escape') {
      if (ev.defaultPrevented) return;
      const close = m.querySelector('[data-close],.mdl-x,.mdl-close,[aria-label^="Close"],.mdl-h button[aria-label*="Close"],button.x');
      if (close) { ev.preventDefault(); close.click(); }
      return;
    }
    if (ev.key !== 'Tab') return;
    const items = [...m.querySelectorAll(SEL)].filter(vis); if (!items.length) return;
    const i = items.indexOf(document.activeElement);
    if (ev.shiftKey && i <= 0) { ev.preventDefault(); items[items.length - 1].focus(); }
    else if (!ev.shiftKey && (i === -1 || i === items.length - 1)) { ev.preventDefault(); items[0].focus(); }
  });
  new MutationObserver(() => {
    const m = topModal();
    if (m && m !== last) { last = m; if (!m.contains(document.activeElement)) { const f = [...m.querySelectorAll(SEL)].filter(vis)[0]; f && f.focus({ preventScroll: true }); } }
    else if (!m) last = null;
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });
})();
