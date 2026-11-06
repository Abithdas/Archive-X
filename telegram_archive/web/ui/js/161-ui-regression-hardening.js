/* ============================================================================
 * FILE: js/161-ui-regression-hardening.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X v29.4.362 — keep viewport overlays outside the zoomed canvas and
   make player breakpoints follow the visible UI width when the app is scaled. */
(function () {
  'use strict';
  const html = document.documentElement;
  let queued = false;
  let sizeObserver = null;
  let mutationObserver = null;

  function appRoot() { return document.getElementById('ax-ui-scale-root'); }
  function effectiveZoom() {
    try {
      if (typeof window.axZoomFactor === 'function') {
        const z = Number(window.axZoomFactor());
        if (Number.isFinite(z) && z > 0) return z;
      }
    } catch (_) { /* use computed-style fallback */ }
    const root = appRoot();
    const rootZoom = root ? parseFloat(getComputedStyle(root).zoom) : 1;
    const htmlZoom = parseFloat(getComputedStyle(html).zoom);
    return (Number.isFinite(htmlZoom) && htmlZoom > 0 ? htmlZoom : 1) *
      (Number.isFinite(rootZoom) && rootZoom > 0 ? rootZoom : 1);
  }
  function update() {
    queued = false;
    const width = Math.max(1, Number(window.innerWidth || html.clientWidth || 1) * effectiveZoom());
    const height = Math.max(1, Number(window.innerHeight || html.clientHeight || 1) * effectiveZoom());
    html.classList.toggle('ax-visual-under-600', width < 600);
    html.classList.toggle('ax-visual-under-768', width < 768);
    html.classList.toggle('ax-visual-under-1000', width < 1000);
    html.classList.toggle('ax-visual-under-1200', width < 1200);
    html.classList.toggle('ax-visual-short-landscape', height <= 500 && width > height);
    const effectiveWidth = `${Math.round(width)}px`;
    // This updater observes root style changes; avoid scheduling another pass
    // when the breakpoint width has not changed.
    if (html.style.getPropertyValue('--ax-effective-ui-width') !== effectiveWidth) {
      html.style.setProperty('--ax-effective-ui-width', effectiveWidth);
    }
  }
  function schedule() {
    if (queued) return;
    queued = true;
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(update);
    else setTimeout(update, 0);
  }
  function ensureOverlayHost() {
    if (!document.body) return null;
    let host = document.getElementById('ax-global-overlay-root');
    const scaleRoot = appRoot();
    if (!host) {
      host = document.createElement('div');
      host.id = 'ax-global-overlay-root';
      host.setAttribute('data-ax-global-overlay-root', '');
      host.setAttribute('aria-live', 'off');
    }
    // Never leave the fixed overlay host under the CSS-zoomed application root.
    if (host.parentElement !== document.body || (scaleRoot && scaleRoot.contains(host))) {
      document.body.appendChild(host);
    }
    return host;
  }

  window.axUpdateVisualBreakpoints = schedule;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { ensureOverlayHost(); schedule(); }, { once: true });
  } else {
    ensureOverlayHost();
    schedule();
  }
  window.addEventListener('resize', schedule, { passive: true });
  window.addEventListener('orientationchange', schedule, { passive: true });
  window.addEventListener('pageshow', () => { ensureOverlayHost(); schedule(); });
  window.addEventListener('visibilitychange', schedule);

  const root = appRoot();
  if (root && typeof ResizeObserver !== 'undefined') {
    sizeObserver = new ResizeObserver(schedule);
    sizeObserver.observe(root);
  }
  if (typeof MutationObserver !== 'undefined' && document.documentElement) {
    mutationObserver = new MutationObserver(schedule);
    mutationObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class', 'data-ui-scale'] });
    if (root) mutationObserver.observe(root, { attributes: true, attributeFilter: ['style', 'class'] });
  }
})();
