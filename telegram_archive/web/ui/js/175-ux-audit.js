/* ============================================================================
 * FILE: js/175-ux-audit.js
 * AREA: Design system and global polish
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X v29.5.400 — UX audit runtime. Small, idempotent guards; no behaviour changes to existing features. */
(function () {
  'use strict';
  if (window.__axUxAudit175) return;
  window.__axUxAudit175 = true;

  /* The A–B repeat popover is anchored to a button near the right of the control row, so on narrow screens it
     could end up partly off-screen. Slide it back inside the viewport whenever it opens. */
  function clampIntoViewport(el) {
    if (!el || el.hidden) { if (el) el.style.transform = ''; return; }
    el.style.transform = '';
    const zoom = Math.max(.1, parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ax-ui-zoom')) || 1);
    const r = el.getBoundingClientRect();
    const margin = 8;
    let dx = 0;
    if (r.left < margin) dx = margin - r.left;
    else if (r.right > window.innerWidth - margin) dx = window.innerWidth - margin - r.right;
    if (dx) el.style.transform = 'translateX(' + Math.round(dx / zoom) + 'px)';
  }
  function bindAbMenu() {
    const menu = document.getElementById('pab-menu');
    if (!menu || menu.dataset.axClamp === '1') return !!menu;
    menu.dataset.axClamp = '1';
    // Observe only `hidden` so our own style writes cannot retrigger the observer.
    new MutationObserver(() => requestAnimationFrame(() => clampIntoViewport(menu)))
      .observe(menu, { attributes: true, attributeFilter: ['hidden'] });
    window.addEventListener('resize', () => clampIntoViewport(menu), { passive: true });
    return true;
  }
  if (!bindAbMenu()) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindAbMenu, { once: true });
    else window.addEventListener('load', bindAbMenu, { once: true });
  }
  /* Keyboard focus return: closing the player (Esc / Back) used to drop focus on <body>, so keyboard users
     restarted from the top of the page. Remember the card that opened the player and restore focus to it
     (or to the re-rendered card for the same video). */
  let lastOpener = null;
  const OPENER = '.vc,.unx,[data-mid],button,[role="button"],a[href]';
  document.addEventListener('pointerdown', ev => { const t = ev.target instanceof Element && ev.target.closest(OPENER); if (t) lastOpener = t; }, true);
  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const t = ev.target instanceof Element && ev.target.closest(OPENER); if (t) lastOpener = t;
  }, true);
  let opener = null;
  window.axPlayerFocusOpened = function () {
    const dlg = document.getElementById('do'); if (!dlg) return;
    opener = lastOpener && !dlg.contains(lastOpener) ? lastOpener : null;
    // Behave like a dialog: keyboard focus moves into the player, so Tab no longer walks the hidden library.
    setTimeout(() => {
      if (!dlg.classList.contains('open') || dlg.contains(document.activeElement)) return;
      const first = document.getElementById('pplaybtn') || dlg.querySelector('button:not([disabled]),[tabindex="0"]');
      if (first && first.getClientRects().length) first.focus({ preventScroll: true });
    }, 60);
  };
  window.axPlayerFocusClosed = function () {
    const dlg = document.getElementById('do');
    const target = opener; opener = null;
    if (!target || !dlg) return;
    const key = target.dataset ? { mid: target.dataset.mid, cid: target.dataset.cid || '' } : null;
    // Cards re-render when the "playing" state changes and the library may soft-refresh
    // just after close, replacing the focused card. Re-check briefly; stop as soon as
    // focus is on anything real (never steal focus the user moved themselves).
    const restore = () => {
      if (dlg.classList.contains('open')) return true;
      const a = document.activeElement;
      if (a && a !== document.body && !dlg.contains(a) && a.isConnected) return false; // focused somewhere real: leave it
      let el = target.isConnected ? target : null;
      if (!el && key && key.mid) {
        el = document.querySelector(`.vc[data-mid="${CSS.escape(key.mid)}"][data-cid="${CSS.escape(key.cid)}"]`)
          || document.querySelector(`.vc[data-mid="${CSS.escape(key.mid)}"]`);
      }
      if (el && el.getClientRects().length) el.focus({ preventScroll: true });
      else if (a && dlg.contains(a)) a.blur();
      return false;
    };
    // Only re-focus while focus is lost (body) or still inside the closed player.
    [180, 500, 900, 1500].forEach(ms => setTimeout(restore, ms));
  };
})();
