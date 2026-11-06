/* ============================================================================
 * FILE: js/43-recommendation-carousels.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* V29.4.204 — progressive enhancement for Up next / Similar carousel rails.
   Preserves the existing recommendation data, click handlers, thumb hydration,
   preview canvases and player navigation; only wraps the rendered card nodes. */
(() => {
  'use strict';
  const roots = ['dnext'];
  const observers = new Map();
  const trackObservers = new WeakSet();
  const resizeObserver = typeof ResizeObserver === 'function'
    ? new ResizeObserver(() => roots.forEach(updateRailById))
    : null;

  function rootById(id) { return document.getElementById(id); }
  function railByRootId(id) {
    return rootById(`${id}-rail`);
  }
  function getTrack(root) { return root ? root.querySelector(':scope > .ax-carousel-track') : null; }

  function ensureTrack(root) {
    if (!root) return null;
    let track = getTrack(root);
    if (!track) {
      const cards = Array.from(root.children).filter(node => node.classList.contains('unx'));
      if (!cards.length) return null;
      track = document.createElement('div');
      track.className = 'ax-carousel-track';
      track.setAttribute('role', 'list');
      track.setAttribute('aria-label', root.id === 'dnext' ? 'Up next videos' : 'Similar videos');
      cards.forEach(card => {
        card.setAttribute('role', 'listitem');
        if (!card.hasAttribute('tabindex')) card.tabIndex = 0;
        track.appendChild(card);
      });
      root.appendChild(track);
    } else {
      // Some render paths append newly loaded cards while an existing track is
      // already present. Fold any direct-child .unx nodes back into the track;
      // otherwise those orphan cards form a broken second row beneath the rail.
      const looseCards = Array.from(root.children).filter(node =>
        node !== track && node.classList?.contains('unx')
      );
      looseCards.forEach(card => {
        card.setAttribute('role', 'listitem');
        if (!card.hasAttribute('tabindex')) card.tabIndex = 0;
        track.appendChild(card);
      });
    }
    track.querySelectorAll(':scope > .unx').forEach(card => {
      if (!card.hasAttribute('role')) card.setAttribute('role', 'listitem');
      if (!card.hasAttribute('tabindex')) card.tabIndex = 0;
    });
    if (!trackObservers.has(track)) {
      trackObservers.add(track);
      track.addEventListener('scroll', () => updateRailById(root.id), { passive: true });
      track.addEventListener('keydown', ev => {
        if (ev.target !== track && !ev.target.classList.contains('unx')) return;
        if (ev.target.classList.contains('unx') && (ev.key === 'Enter' || ev.key === ' ')) {
          ev.preventDefault();
          ev.target.click();
          return;
        }
        if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
          ev.preventDefault();
          track.scrollBy({ left: (ev.key === 'ArrowRight' ? 1 : -1) * Math.max(180, track.clientWidth * .78), behavior: 'smooth' });
        }
      });
      if (resizeObserver) resizeObserver.observe(track);
    }
    return track;
  }

  function updateRailById(id) {
    const root = rootById(id);
    const rail = railByRootId(id);
    if (!root || !rail) return;
    const track = ensureTrack(root);
    const overflow = !!track && track.scrollWidth > track.clientWidth + 3;
    rail.dataset.overflow = overflow ? 'true' : 'false';
    const left = rail.querySelector('.ax-rail-arrow-left');
    const right = rail.querySelector('.ax-rail-arrow-right');
    if (!track) {
      if (left) left.disabled = true;
      if (right) right.disabled = true;
      return;
    }
    const atStart = track.scrollLeft <= 2;
    const atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 3;
    if (left) left.disabled = !overflow || atStart;
    if (right) right.disabled = !overflow || atEnd;
    // YouTube-style arrows sit on the vertical centerline of the thumbnail,
    // not the centerline of the whole card (which also includes title/metadata).
    const railRect = rail.getBoundingClientRect();
    const trackRect = track.getBoundingClientRect();
    const firstArt = track.querySelector('.unx-art');
    const artRect = firstArt ? firstArt.getBoundingClientRect() : trackRect;
    [left, right].filter(Boolean).forEach(button => {
      const top = artRect.top - railRect.top + (artRect.height - button.offsetHeight) / 2;
      button.style.top = `${Math.round(Math.max(0, top))}px`;
    });
  }

  function observeRoot(id) {
    const root = rootById(id);
    if (!root || observers.has(id)) return;
    const observer = new MutationObserver(() => {
      // Normalize nodes synchronously so transient late-arriving cards cannot
      // flash below the horizontal row. Then measure the final rail geometry.
      ensureTrack(root);
      requestAnimationFrame(() => updateRailById(id));
    });
    observer.observe(root, { childList: true });
    observers.set(id, observer);
    updateRailById(id);
    if (resizeObserver) resizeObserver.observe(root);
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('.ax-rail-arrow[data-rail-target]');
    if (!button || button.disabled) return;
    const targetId = button.dataset.railTarget;
    const root = rootById(targetId);
    const track = ensureTrack(root);
    if (!track) return;
    const direction = Number(button.dataset.railDirection || 1);
    track.scrollBy({ left: direction * Math.max(200, track.clientWidth * .82), behavior: 'smooth' });
    requestAnimationFrame(() => updateRailById(targetId));
  });

  function init() {
    roots.forEach(observeRoot);
    window.addEventListener('resize', () => roots.forEach(updateRailById), { passive: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
