/* ============================================================================
 * FILE: js/137-ax2-video-page.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X — New Look, Step 3: presentation-only player-page shell glue.
   Does not alter playback, autoplay selection, streaming, or preview logic. */
(() => {
  'use strict';

  const page = document.getElementById('do');
  if (!page || page.dataset.ax2VideoPageBound === '1') return;
  page.dataset.ax2VideoPageBound = '1';

  const labelForAutoplayMode = () => {
    let mode = 'context';
    try { mode = String(localStorage.getItem('ta_autonext_mode') || 'context'); } catch (_) { /* Private browsing may block storage. */ }
    const labels = {
      context: 'Current queue',
      recommended: 'Recommended',
      performer: 'By performer',
      studio: 'By studio',
    };
    return labels[mode] || 'Current queue';
  };

  const syncUpNextSource = () => {
    const source = document.getElementById('dnext-source');
    if (!source) return;
    const label = labelForAutoplayMode();
    if (source.textContent !== label) source.textContent = label;
  };

  const syncHeaderSurface = () => {
    const bar = document.getElementById('dtopbar');
    if (!bar) return;
    bar.classList.toggle('is-scrolled', page.scrollTop > 8);
  };

  page.addEventListener('scroll', syncHeaderSurface, { passive: true });
  window.addEventListener('storage', event => {
    if (!event.key || event.key === 'ta_autonext_mode') syncUpNextSource();
  });

  // Shell navigation must close the player first, then allow its original inline
  // sv(...) handler to navigate. This is a capture listener, not a playback hook.
  document.addEventListener('click', event => {
    if (!page.classList.contains('open')) return;
    const navItem = event.target instanceof Element
      ? event.target.closest('#ax-sidebar .ax-side-item, .ax-ux-bottom-nav [data-ux-bottom]')
      : null;
    if (!navItem) return;
    if (typeof window.closeDBtn === 'function') window.closeDBtn(true);
  }, true);

  const observer = new MutationObserver(() => {
    if (page.classList.contains('open')) {
      syncUpNextSource();
      syncHeaderSurface();
    }
  });
  observer.observe(page, { attributes: true, attributeFilter: ['class'] });
  syncUpNextSource();
  syncHeaderSurface();
})();
