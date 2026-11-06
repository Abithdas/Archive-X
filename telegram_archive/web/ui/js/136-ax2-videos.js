/* ============================================================================
 * FILE: js/136-ax2-videos.js
 * AREA: Design system and global polish
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Filter sheet
 *   - Favorites and map menus
 *   - Library summary and cards
 *   - Gestures and init
 * ============================================================================ */
/* AX2 Step 2 — Videos Library presentation glue. Existing data and actions remain authoritative. */
(function () {
  'use strict';
  if (window.__ax2VideosInitialized) return;
  window.__ax2VideosInitialized = true;


  // ------------------------------------------------------------------
  // SECTION: Filter sheet
  // Sheet control and filter chips.
  // ------------------------------------------------------------------
  const $ = (id, root = document) => root.getElementById ? root.getElementById(id) : root.querySelector(`#${CSS.escape(id)}`);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const transitionWait = () => reducedMotion() ? 1 : 260;

  function openSheet(sheet, trigger) {
    if (!sheet) return;
    if (window.ax2 && typeof window.ax2.openSheet === 'function') window.ax2.openSheet(sheet, trigger || null);
    else {
      sheet.hidden = false;
      sheet.setAttribute('aria-hidden', 'false');
      requestAnimationFrame(() => sheet.classList.add('open'));
    }
  }
  function closeSheet(sheet) {
    if (!sheet) return;
    if (window.ax2 && typeof window.ax2.closeSheet === 'function') window.ax2.closeSheet(sheet);
    else {
      sheet.classList.remove('open');
      sheet.setAttribute('aria-hidden', 'true');
      window.setTimeout(() => { sheet.hidden = true; }, transitionWait());
    }
  }

  function syncFilterChips() {
    const draftResolution = $('ux-f-res')?.value || '';
    $$('[data-ax2-resolution]').forEach(btn => {
      const selected = String(btn.dataset.ax2Resolution || '') === draftResolution;
      btn.classList.toggle('is-active', selected);
      btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    const from = $('ux-f-d1')?.value || '';
    const to = $('ux-f-d2')?.value || '';
    $$('[data-ax2-date-range]').forEach(btn => {
      const days = Number(btn.dataset.ax2DateRange || 0);
      let selected = false;
      if (from && to && days > 0) {
        const end = new Date(`${to}T12:00:00`);
        const start = new Date(`${from}T12:00:00`);
        const delta = Math.round((end.getTime() - start.getTime()) / 86400000);
        const today = new Date(); today.setHours(12, 0, 0, 0);
        const toDate = new Date(`${to}T12:00:00`);
        selected = delta >= days - 1 && delta <= days + 1 && Math.abs((today - toDate) / 86400000) <= 1;
      }
      btn.classList.toggle('is-active', selected);
      btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
  }
  function setLocalDateInput(input, date) {
    if (!input) return;
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    input.value = `${year}-${month}-${day}`;
  }
  window.ax2SetDateRange = function (days, trigger) {
    const start = new Date(); start.setHours(12, 0, 0, 0); start.setDate(start.getDate() - Math.max(1, Number(days) || 7));
    const end = new Date(); end.setHours(12, 0, 0, 0);
    setLocalDateInput($('ux-f-d1'), start);
    setLocalDateInput($('ux-f-d2'), end);
    syncFilterChips();
    trigger?.setAttribute('aria-pressed', 'true');
  };
  window.ax2SetResolution = function (value) {
    const select = $('ux-f-res');
    if (select) select.value = String(value ?? '');
    syncFilterChips();
  };

  const originalOpenFilters = window.uxOpenFilters;
  const originalCloseFilters = window.uxCloseFilters;
  const originalApplyFilters = window.uxApplyFilters;
  const originalResetFilters = window.uxResetFilters;
  window.uxOpenFilters = function (trigger) {
    const sheet = $('ux-filter-drawer');
    if (!sheet) return;
    if (sheet.classList.contains('open')) { closeSheet(sheet); return; }
    if (typeof originalOpenFilters === 'function') originalOpenFilters();
    // The legacy helper fills draft inputs but also opens the drawer directly;
    // remove that legacy open state so AX2 can establish focus, scrim and scroll lock.
    sheet.classList.remove('open', 'closing');
    sheet.hidden = false;
    sheet.setAttribute('aria-hidden', 'false');
    syncFilterChips();
    openSheet(sheet, trigger || $('fbtn'));
  };
  window.uxCloseFilters = function () {
    const sheet = $('ux-filter-drawer');
    if (sheet && sheet.classList.contains('open')) closeSheet(sheet);
    else if (typeof originalCloseFilters === 'function') originalCloseFilters();
  };
  window.uxResetFilters = function () {
    if (typeof originalResetFilters === 'function') originalResetFilters();
    syncFilterChips();
  };
  window.uxApplyFilters = function () {
    if (typeof originalApplyFilters === 'function') originalApplyFilters();
    const sheet = $('ux-filter-drawer');
    if (sheet && !sheet.hidden && !sheet.classList.contains('closing')) closeSheet(sheet);
    syncFilterChips();
  };

  window.uxOpenLibraryMore = function (trigger) {
    const sheet = $('ax2-library-more-sheet');
    if (!sheet) return;
    openSheet(sheet, trigger || $('ax2-library-more-trigger'));
  };
  window.ax2CloseLibraryMore = function () { closeSheet($('ax2-library-more-sheet')); };
  window.ax2RunLibraryAction = function (action) {
    const sheet = $('ax2-library-more-sheet');
    if (sheet && (sheet.classList.contains('open') || !sheet.hidden)) closeSheet(sheet);
    window.setTimeout(() => {
      if (typeof action === 'function') {
        try { const result = action(); if (result && typeof result.catch === 'function') result.catch(error => window.toast?.(String(error?.message || error))); }
        catch (error) { window.toast?.(String(error?.message || error)); }
      }
    }, sheet && !sheet.hidden ? transitionWait() : 0);
  };
  window.ax2RunSelectedChannel = function (action) {
    const selected = $$(' #channel-cards .pcard[data-channel-id].selected').map(card => String(card.dataset.channelId || '')).filter(Boolean);
    if (selected.length !== 1) { window.toast?.('Select one channel first.'); return; }
    if (action === 'shuffle' && typeof window.openChannelShufflePlayerChoice === 'function') return window.openChannelShufflePlayerChoice(selected[0]);
    if (action === 'edit' && typeof window.editChannelCard === 'function') return window.editChannelCard(selected[0]);
  };

  const originalCloseMapMenus = window.closeMapMenus;

  // ------------------------------------------------------------------
  // SECTION: Favorites and map menus
  // Map menu state and favorite sync.
  // ------------------------------------------------------------------
  function setMapMenuState(panel, open) {
    if (!panel) return;
    panel.classList.toggle('open', open);
    panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    const trigger = panel.closest('.map-menu')?.querySelector(':scope > button');
    trigger?.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  window.toggleMapMenu = function (id) {
    const panel = $(id);
    if (!panel) return;
    window.dismissAx2LayerForMapping?.();
    const wasOpen = panel.classList.contains('open');
    if (typeof window.closeMapMenus === 'function') window.closeMapMenus(id);
    setMapMenuState(panel, !wasOpen);
  };
  window.closeMapMenus = function (except = '') {
    $$('.map-menu-panel').forEach(panel => {
      if (panel.id !== except) setMapMenuState(panel, false);
    });
    if (typeof originalCloseMapMenus === 'function') {
      // Keep the original helper authoritative for any legacy panel markup.
      try { originalCloseMapMenus(except); } catch (_) { /* presentation-only fallback */ }
    }
  };
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const panel = document.querySelector('.map-menu-panel.open');
    if (!panel) return;
    event.preventDefault();
    event.stopPropagation();
    const trigger = panel.closest('.map-menu')?.querySelector(':scope > button');
    window.closeMapMenus();
    trigger?.focus({ preventScroll: true });
  }, true);

  const originalTgFav = window.tgFav;
  function setAttrIfChanged(node, name, value) {
    if (node && node.getAttribute(name) !== String(value)) node.setAttribute(name, String(value));
  }
  function syncFavState() {
    const button = $('favbtn');
    if (!button) return;
    // The existing filter-chip renderer is the canonical reflection of _favOnly.
    const active = /Favourites/i.test($('afc')?.textContent || '');
    button.classList.toggle('is-on', active);
    setAttrIfChanged(button, 'aria-pressed', active ? 'true' : 'false');
    if (button.style.borderColor) button.style.borderColor = '';
  }
  if (typeof originalTgFav === 'function') {
    window.tgFav = function (...args) {
      const result = originalTgFav.apply(this, args);
      requestAnimationFrame(syncFavState);
      return result;
    };
  }


  // ------------------------------------------------------------------
  // SECTION: Library summary and cards
  // Wrapped renderers, summary and card tags.
  // ------------------------------------------------------------------
  const originalRenderChips = window.renderChips;
  if (typeof originalRenderChips === 'function') {
    window.renderChips = function (...args) {
      const result = originalRenderChips.apply(this, args);
      requestAnimationFrame(syncLibraryState);
      return result;
    };
  }
  const originalRenderChannelCards = window.renderChannelCards;
  if (typeof originalRenderChannelCards === 'function') {
    window.renderChannelCards = function (...args) {
      const result = originalRenderChannelCards.apply(this, args);
      requestAnimationFrame(syncLibraryState);
      return result;
    };
  }

  function syncCompactLibrarySummary() {
    const summary = $('rs');
    if (!summary) return;
    const raw = summary.textContent.trim();
    const match = raw.match(/of\s+([\d,]+)\s+videos?/i);
    if (!match) return;
    const total = Number(match[1].replace(/,/g, ''));
    if (!Number.isFinite(total)) return;
    const selectedCount = $$(' #channel-cards .pcard[data-channel-id].selected').length;
    const next = `${total.toLocaleString()} video${total === 1 ? '' : 's'}${selectedCount ? ` · ${selectedCount} channel${selectedCount === 1 ? '' : 's'}` : ''}`;
    if (summary.textContent.trim() !== next) summary.textContent = next;
  }
  function syncLibraryState() {
    const track = $('channel-cards');
    const channels = track ? $$(' .pcard[data-channel-id]', track) : [];
    const selected = channels.filter(card => card.classList.contains('selected'));
    const count = $('ax2-selected-channel-count');
    if (count) {
      count.textContent = `${selected.length} channel${selected.length === 1 ? '' : 's'} selected`;
      count.hidden = selected.length === 0;
    }
    const canRun = selected.length === 1;
    ['ax2-selected-channel-shuffle', 'ax2-selected-channel-edit'].forEach(id => {
      const button = $(id);
      if (button) { button.disabled = !canRun; button.title = canRun ? `Use selected channel: ${selected[0]?.querySelector('.ax2-channel-name')?.textContent?.trim() || 'selected channel'}` : 'Select one channel'; }
    });
    const all = track?.querySelector('.ax2-channel-all');
    const allSelected = channels.length > 0 && selected.length === channels.length;
    if (all) { all.classList.toggle('selected', allSelected); setAttrIfChanged(all, 'aria-pressed', allSelected ? 'true' : 'false'); }
    const pipeline = $('library-bg-pipeline');
    if (pipeline) pipeline.classList.toggle('is-active', !!pipeline.textContent.trim());
    syncFavState();
    syncCompactLibrarySummary();
    const sort = $('tb-sort');
    const label = document.querySelector('[data-ax2-sort-label]');
    if (sort && label) {
      const text = sort.options[sort.selectedIndex]?.textContent?.trim() || 'Newest first';
      if (label.textContent !== text) label.textContent = text;
    }
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>\"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;' }[ch]));
  }
  function syncLibraryCardTags(root) {
    const presenter = window._smartTagPresentation;
    if (typeof presenter !== 'function') return;
    $$('.vc.ax2-library-card .video-card-tags', root || $('vg') || document).forEach(box => {
      const card = box.closest('.vc.ax2-library-card');
      if (!card) return;
      const video = typeof window.findVideo === 'function' ? window.findVideo(card.dataset.mid, card.dataset.cid) : null;
      if (!video) return;
      const presentation = presenter(Array.isArray(video.tags) ? video.tags : []);
      const visible = (presentation.tags || []).slice(0, 2);
      const tones = ['accent', 'violet', 'ok', 'sky'];
      let html = visible.map((tag, index) => {
        const arg = JSON.stringify(String(tag)).replace(/</g, "\\u003c").replace(/"/g, "&quot;");
        return `<span class="video-tag-chip ax2-tag-tone-${tones[index % tones.length]}" title="${escapeHtml(tag)}" onclick="event.stopPropagation();openTagByName(${arg})">${escapeHtml(tag)}</span>`;
      }).join('');
      const hiddenCount = Math.max(0, (presentation.tags || []).length - visible.length) + (presentation.categories || []).length;
      if (hiddenCount) html += `<span class="video-more-chip" title="${hiddenCount} additional tag or category items">+${hiddenCount}</span>`;
      // Compare against a stored key, not box.innerHTML: other runtimes add role/tabindex/aria-label
      // to the chips after render, so the serialized markup never matched and this rewrote every card's
      // chips on every frame (an endless mutation loop that caused idle CPU load and flicker).
      if (box.dataset.axTagsKey !== html) { box.innerHTML = html; box.dataset.axTagsKey = html; }
    });
  }
  function decorateGrid() {
    const grid = $('vg');
    if (!grid) return;
    const cards = $$('.vc', grid).filter(card => card.parentElement === grid);
    cards.forEach((card, index) => {
      card.classList.add('ax2-library-card');
      if (!card.classList.contains('ax2-video-enter')) {
        card.classList.add('ax2-video-enter');
        card.style.setProperty('--ax2-grid-delay', `${index < 12 ? index * 30 : 330}ms`);
      }
    });
    syncLibraryCardTags(grid);
    const empty = grid.querySelector('.es');
    // Only offer "Clear filters" when the grid is empty because of filters, not because no channel is picked.
    if (empty && !empty.querySelector('.ax2-empty-clear') && !empty.querySelector('[onclick^="chAll"]')) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'ax2-empty-clear';
      clear.textContent = 'Clear filters';
      clear.setAttribute('aria-label', 'Clear Library filters');
      clear.addEventListener('click', () => { if (typeof window.resetF === 'function') window.resetF(); });
      empty.appendChild(clear);
    }
  }


  // ------------------------------------------------------------------
  // SECTION: Gestures and init
  // Swipe handling and startup.
  // ------------------------------------------------------------------
  function bindSheetSwipe(sheet) {
    if (!sheet || sheet.dataset.ax2SwipeBound === '1') return;
    sheet.dataset.ax2SwipeBound = '1';
    let startY = 0, startTime = 0, tracking = false;
    sheet.addEventListener('pointerdown', event => {
      if (window.innerWidth >= 600 || !event.target.closest('[data-ax2-sheet-handle], [data-ax2-sheet-head]')) return;
      startY = event.clientY; startTime = performance.now(); tracking = true;
    }, { passive: true });
    sheet.addEventListener('pointerup', event => {
      if (!tracking) return;
      tracking = false;
      const delta = event.clientY - startY;
      const elapsed = Math.max(1, performance.now() - startTime);
      if (delta > 80 || (delta > 35 && elapsed < 180)) closeSheet(sheet);
    }, { passive: true });
    sheet.addEventListener('pointercancel', () => { tracking = false; }, { passive: true });
  }

  function init() {
    const sort = $('tb-sort');
    if (sort) sort.addEventListener('change', () => {
      const label = document.querySelector('[data-ax2-sort-label]');
      if (label) label.textContent = sort.options[sort.selectedIndex]?.textContent?.trim() || 'Newest first';
    });
    const reloadButton = $('scan-btn');
    if (reloadButton) {
      const restoreReloadIcon = () => {
        if (reloadButton.textContent.trim() === 'Reload selected' && !reloadButton.querySelector('.ax-icon')) {
          reloadButton.innerHTML = '<svg class="ax-icon" aria-hidden="true"><use href="#ax-refresh"></use></svg><span>Reload selected</span>';
        }
      };
      new MutationObserver(restoreReloadIcon).observe(reloadButton, { childList: true, subtree: true });
      restoreReloadIcon();
    }
    ['ux-f-studio', 'ux-f-perf', 'ux-f-tag', 'ux-f-d1', 'ux-f-d2', 'ux-f-res'].forEach(id => {
      $(id)?.addEventListener('input', syncFilterChips);
      $(id)?.addEventListener('change', syncFilterChips);
    });
    ['ux-filter-drawer', 'ax2-library-more-sheet'].forEach(id => bindSheetSwipe($(id)));

    // Keep the real legacy callbacks intact; use the AX2 sheet only as a visual host.
    document.addEventListener('click', event => {
      const row = event.target.closest('#ax2-library-more-sheet #index-btn, #ax2-library-more-sheet #scan-btn');
      if (!row) return;
      // These action rows call ax2RunLibraryAction() inline, which closes the sheet
      // before invoking the original index/reload callback.
    }, true);

    const track = $('channel-cards');
    if (track) {
      track.addEventListener('wheel', event => {
        if (event.defaultPrevented || event.ctrlKey || event.shiftKey ||
            !event.deltaY || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
        const maxScroll = track.scrollWidth - track.clientWidth;
        if (maxScroll <= 1) return;
        const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
          ? 16
          : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
            ? track.clientWidth
            : 1;
        const nextScroll = Math.max(0, Math.min(maxScroll, track.scrollLeft + event.deltaY * unit));
        if (Math.abs(nextScroll - track.scrollLeft) < 1) return;
        event.preventDefault();
        track.scrollLeft = nextScroll;
      }, { passive: false });
      const observer = new MutationObserver(() => requestAnimationFrame(syncLibraryState));
      observer.observe(track, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'aria-pressed'] });
    }
    const activeFilters = $('afc');
    if (activeFilters) {
      const observer = new MutationObserver(() => requestAnimationFrame(syncLibraryState));
      observer.observe(activeFilters, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    }
    const summary = $('rs');
    if (summary) {
      const observer = new MutationObserver(() => requestAnimationFrame(syncCompactLibrarySummary));
      observer.observe(summary, { childList: true, characterData: true, subtree: true });
    }
    const pipeline = $('library-bg-pipeline');
    if (pipeline) {
      const observer = new MutationObserver(() => requestAnimationFrame(syncLibraryState));
      observer.observe(pipeline, { childList: true, characterData: true, subtree: true });
    }
    const grid = $('vg');
    if (grid) {
      const observer = new MutationObserver(() => requestAnimationFrame(decorateGrid));
      observer.observe(grid, { childList: true, subtree: true });
      decorateGrid();
    }
    const mapbar = $('mapbar');
    if (mapbar) {
      const observer = new MutationObserver(() => document.body.classList.toggle('ax2-select-mode-active', getComputedStyle(mapbar).display !== 'none'));
      observer.observe(mapbar, { attributes: true, attributeFilter: ['style', 'class'] });
      document.body.classList.toggle('ax2-select-mode-active', getComputedStyle(mapbar).display !== 'none');
    }
    syncLibraryState();
    syncFilterChips();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
