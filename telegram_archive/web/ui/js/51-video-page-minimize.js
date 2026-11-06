/* ============================================================================
 * FILE: js/51-video-page-minimize.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X V29.4.204 — compact visible details, functional rail previews, arrow access and Transfers placement. */
(() => {
  'use strict';
  const TAG_LIMIT = 12;
  const MARKER_LIMIT = 4;
  let tagObserver = null;
  let markerObserver = null;

  const byId = id => document.getElementById(id);

  function buildCompactHeader(type) {
    const head = byId(type === 'tags' ? 'dtag-head' : 'dmarker-head');
    if (!head) return null;
    const existing = head.closest(`.ax-detail-compact-head[data-detail-kind="${type}"]`);
    if (existing) return existing;
    const parent = head.parentElement;
    if (!parent) return null;
    let row = null;
    if (!row) {
      row = document.createElement('div');
      row.className = 'ax-detail-compact-head';
      row.dataset.detailKind = type;
      parent.insertBefore(row, head);
      row.appendChild(head);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ax-detail-more-btn';
      button.dataset.detailKind = type;
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        if (type === 'tags') window.toggleDetailTags?.();
        else window.toggleDetailMarkers?.();
      });
      row.appendChild(button);
    }
    return row;
  }

  function detailCount(type) {
    const box = byId(type === 'tags' ? 'dtags' : 'v55-markers');
    return box ? box.querySelectorAll(type === 'tags' ? '.tag' : '.v55-marker').length : 0;
  }

  function applyCompactState(type, reset = false) {
    const box = byId(type === 'tags' ? 'dtags' : 'v55-markers');
    const head = byId(type === 'tags' ? 'dtag-head' : 'dmarker-head');
    const row = buildCompactHeader(type);
    const button = row?.querySelector('.ax-detail-more-btn');
    if (!box || !head || !button) return;

    if (reset || !['0', '1'].includes(box.dataset.axDetailExpanded)) box.dataset.axDetailExpanded = '0';
    const expanded = box.dataset.axDetailExpanded === '1';
    box.classList.add('ax-detail-compact');
    box.classList.toggle('ax-detail-expanded', expanded);
    // Do not collapse the section. Older handlers/styles used these classes to hide the full list.
    box.classList.remove('tags-collapsed', 'markers-collapsed');
    if (type === 'tags') box.classList.add('tags-expanded');

    const count = detailCount(type);
    const limit = type === 'tags' ? TAG_LIMIT : MARKER_LIMIT;
    button.hidden = count <= limit;
    button.textContent = expanded ? 'Show less' : `Show all${count ? ` (${count})` : ''}`;
    button.setAttribute('aria-expanded', String(expanded));
    button.setAttribute('aria-label', `${expanded ? 'Show fewer' : 'Show all'} scene ${type}`);
    head.classList.toggle('expanded', expanded);
    head.setAttribute('aria-expanded', String(expanded));
  }

  function toggleCompact(type) {
    const box = byId(type === 'tags' ? 'dtags' : 'v55-markers');
    if (!box) return;
    box.dataset.axDetailExpanded = box.dataset.axDetailExpanded === '1' ? '0' : '1';
    applyCompactState(type, false);
  }

  function initDetails() {
    buildCompactHeader('tags');
    buildCompactHeader('markers');
    applyCompactState('tags', true);
    applyCompactState('markers', true);
    const tags = byId('dtags');
    const markers = byId('v55-markers');
    if (tagObserver) tagObserver.disconnect();
    if (markerObserver) markerObserver.disconnect();
    if (window.MutationObserver && tags) {
      tagObserver = new MutationObserver(() => applyCompactState('tags', true));
      tagObserver.observe(tags, { childList: true });
    }
    if (window.MutationObserver && markers) {
      markerObserver = new MutationObserver(() => applyCompactState('markers', true));
      markerObserver.observe(markers, { childList: true });
    }
    // The marker-count helper writes textContent into the heading; update button copy without replacing the control.
    [byId('dtag-head'), byId('dmarker-head')].filter(Boolean).forEach(head => {
      const type = head.id === 'dtag-head' ? 'tags' : 'markers';
      if (head.dataset.axCountObserve === '1' || !window.MutationObserver) return;
      head.dataset.axCountObserve = '1';
      const observer = new MutationObserver(() => applyCompactState(type, false));
      observer.observe(head, { childList: true, characterData: true, subtree: true });
    });
  }

  // Replace the old whole-section toggles with a manual Show all/Show less behavior.
  window.toggleDetailTags = () => toggleCompact('tags');
  window.toggleDetailMarkers = () => toggleCompact('markers');

  // Keep the shared preview-layer helper from 09-app.js. It also toggles the
  // active canvas class required to reveal frames inside the nested preview deck.

  // Verify the Metadata & Assets Manager is physically part of the Transfers page.
  // Its setup controls remain in the HTML frontend; live job status is intentionally centralized in Live Lane and Jobs.
  function verifyMetadataManagerLocation() {
    const slot = byId('ax-transfer-metadata-slot');
    const card = slot?.querySelector('[data-ops-module="metadata"]');
    const intact = !!(slot && card && card.querySelector('#u-assets-run-selected'));
    if (!intact) console.warn('[Archive X] Metadata & Assets Manager was not found in Transfers.');
    return intact;
  }

  function removeWorkShortcuts() {
    document.querySelectorAll('#ax-sidebar .ax-side-item').forEach(item => {
      const label = (item.querySelector('span:last-child')?.textContent || item.textContent || '').trim().toLowerCase();
      if (label === 'recent' || label === 'favourites' || label === 'favorites') item.remove();
    });
  }

  // Dismiss floating hover cards when the pointer reaches a carousel arrow.
  // The CSS raises the arrow above the popover; this handler then removes the popover
  // synchronously so the arrow remains targetable and its scroll click is not intercepted.
  document.addEventListener('pointerover', event => {
    const arrow = event.target?.closest?.('.ax-rail-arrow');
    if (!arrow) return;
    const card = document.querySelector('.ax-hover-card-source');
    if (card && typeof window._axDismissHoverCardForArrow === 'function') {
      window._axDismissHoverCardForArrow(card);
    }
  }, true);

  function init() {
    initDetails();
    verifyMetadataManagerLocation();
    removeWorkShortcuts();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
