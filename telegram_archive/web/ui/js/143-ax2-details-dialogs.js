/* ============================================================================
 * FILE: js/143-ax2-details-dialogs.js
 * AREA: Dialogs and overlays
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Toasts
 *   - Dialog layer tracking
 *   - Bio toggles
 *   - Busy state wrappers
 *   - Connection banner
 *   - File input names
 * ============================================================================ */
/* Archive X Step 9 — detail-page interaction polish, accessible layers, toasts and live states. */
(function () {
  'use strict';
  if (window.__ax2DetailsDialogsReady) return;
  window.__ax2DetailsDialogsReady = true;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const phone = () => !!(window.matchMedia && window.matchMedia('(max-width:599px), (max-height:500px) and (orientation:landscape)').matches);
  const reduceMotion = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const icon = name => `<svg class="ax-icon" aria-hidden="true"><use href="#ax-${name}"></use></svg>`;
  const setAttrIfChanged = (node, name, value) => { const next = String(value); if (node && node.getAttribute(name) !== next) node.setAttribute(name, next); };
  const setTextIfChanged = (node, value) => { const next = String(value ?? ''); if (node && node.textContent !== next) node.textContent = next; };


  // ------------------------------------------------------------------
  // SECTION: Toasts
  // Toast host, tones and dismissal.
  // ------------------------------------------------------------------
  /* A toast stack replaces the old single-message element without changing any call sites. */
  const toastHost = () => {
    let host = $('#toast');
    if (!host) {
      host = document.createElement('div');
      host.id = 'toast'; host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite');
      ($('#ax-ui-scale-root') || document.body).appendChild(host);
    }
    return host;
  };
  /* Modal <dialog> elements live in the browser's top layer, which no z-index can beat, so a
     normal toast rendered behind the dialog's blurred ::backdrop (Scene Match Review, quick edit,
     ...). Two things are needed:
       1. the host is a manual popover, so it is promoted into the top layer too, and showing it
          again for each message keeps it above whichever dialog was opened last;
       2. while a modal dialog is open everything outside it is inert, so the host is moved INTO
          that dialog, otherwise the toast would be visible but its buttons could not be clicked.
     It returns to its normal place when the dialog closes. Browsers without the Popover API keep
     the old behaviour. */
  let toastHome = null;
  const toastTopLayer = (host, show) => {
    try {
      if (!toastHome) toastHome = host.parentElement;
      let modal = null;
      if (show) { try { modal = document.querySelector('dialog[open]:modal'); } catch (_) { modal = null; } }
      const target = modal || toastHome;
      if (target && host.parentElement !== target) target.appendChild(host);
      if (typeof host.showPopover !== 'function') return;
      if (!host.hasAttribute('popover')) host.setAttribute('popover', 'manual');
      if (host.matches(':popover-open')) host.hidePopover();
      if (show) host.showPopover();
    } catch (_) { /* leave the toast in the page */ }
  };
  document.addEventListener('close', event => {
    const host = $('#toast');
    if (event.target instanceof HTMLDialogElement && host && event.target.contains(host)) {
      // The dialog (and the toast inside it) is going away: put the toast back and keep it showing.
      toastTopLayer(host, !host.hidden && host.querySelector('.ax2-toast-item') !== null);
    }
  }, true);
  const activeToasts = new Map();
  let toastSequence = 0;
  function inferTone(message) {
    const text = String(message || '').trim();
    const low = text.toLowerCase();
    if (/^(could not|failed\b)/i.test(text) || /\b(failed|failure|error|cannot reach)\b/i.test(low)) return 'error';
    if (/^(choose|enter|select|must)\b/i.test(text) || low.includes('no longer available')) return 'warning';
    if (/\b(saved|completed|queued|created|exported|reset|copied|recovered|started|refreshed)\b/i.test(low)) return 'success';
    return 'info';
  }
  function toastIcon(tone) { return tone === 'success' ? 'check' : tone === 'warning' || tone === 'error' ? 'alert' : 'info'; }
  function removeToast(id, immediate = false) {
    const state = activeToasts.get(id);
    if (!state) return;
    activeToasts.delete(id);
    clearTimeout(state.timer);
    const finish = () => {
      state.node.remove();
      const host = $('#toast');
      if (host && !host.querySelector('.ax2-toast-item')) { host.hidden = true; toastTopLayer(host, false); }
    };
    if (immediate || reduceMotion()) finish();
    else { state.node.classList.remove('is-visible'); setTimeout(finish, 280); }
  }
  function pauseToast(id, paused) {
    const state = activeToasts.get(id);
    if (!state || state.tone === 'error' || state.persistent) return;
    if (paused) {
      if (state.timer) { clearTimeout(state.timer); state.timer = 0; state.remaining = Math.max(0, state.deadline - Date.now()); }
    } else if (!state.timer && state.remaining >= 0) {
      state.deadline = Date.now() + state.remaining;
      state.timer = setTimeout(() => removeToast(id), state.remaining);
    }
  }
  window.toast = function ax2Toast(message, ms = undefined, options = undefined) {
    if (ms && typeof ms === 'object') { options = ms; ms = undefined; }
    options = options && typeof options === 'object' ? options : {};
    const host = toastHost();
    host.hidden = false;
    const raw = String(message ?? '');
    const tone = ['success', 'info', 'warning', 'error'].includes(options.tone) ? options.tone : inferTone(raw);
    const colon = raw.indexOf(': ');
    const title = colon > 0 ? raw.slice(0, colon).trim() : raw;
    const detail = colon > 0 ? raw.slice(colon + 2).trim() : '';
    const id = ++toastSequence;
    const node = document.createElement('div');
    node.className = 'ax2-toast-item'; node.dataset.tone = tone; node.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    const safeTitle = document.createElement('div'); safeTitle.className = 'ax2-toast-title'; safeTitle.textContent = title || raw || 'Archive X';
    const copy = document.createElement('div'); copy.className = 'ax2-toast-copy'; copy.appendChild(safeTitle);
    if (detail && detail !== title) { const detailNode = document.createElement('div'); detailNode.className = 'ax2-toast-detail'; detailNode.textContent = detail; copy.appendChild(detailNode); }
    const iconNode = document.createElement('span'); iconNode.className = 'ax2-toast-icon'; iconNode.innerHTML = icon(toastIcon(tone));
    const actions = document.createElement('div'); actions.className = 'ax2-toast-actions';
    let action = options.action && typeof options.action === 'object' ? options.action : null;
    if (!action && /\boperations\b/i.test(raw)) action = { label: 'Open', run: () => { if (typeof window.sv === 'function') window.sv('admin'); } };
    if (action && typeof action.run === 'function' && action.label) {
      const link = document.createElement('button'); link.type = 'button'; link.className = 'ax2-toast-action'; link.textContent = String(action.label);
      link.addEventListener('click', () => { try { action.run(); } finally { if (action.dismiss !== false) removeToast(id, true); } }); actions.appendChild(link);
    }
    const close = document.createElement('button'); close.type = 'button'; close.className = 'ax2-toast-close'; close.setAttribute('aria-label', 'Dismiss notification'); close.innerHTML = icon('close'); close.addEventListener('click', () => removeToast(id, true)); actions.appendChild(close);
    node.append(iconNode, copy, actions);
    node.addEventListener('mouseenter', () => pauseToast(id, true)); node.addEventListener('mouseleave', () => pauseToast(id, false));
    node.addEventListener('focusin', () => pauseToast(id, true)); node.addEventListener('focusout', event => { if (!node.contains(event.relatedTarget)) pauseToast(id, false); });
    const pauseWhileTouching = event => {
      if (event.pointerType === 'touch' || event.pointerType === 'pen') pauseToast(id, true);
    };
    const resumeAfterTouch = event => {
      if ((event.pointerType === 'touch' || event.pointerType === 'pen') && !node.contains(document.activeElement)) pauseToast(id, false);
    };
    node.addEventListener('pointerdown', pauseWhileTouching);
    node.addEventListener('pointerup', resumeAfterTouch);
    node.addEventListener('pointercancel', resumeAfterTouch);
    host.appendChild(node);
    toastTopLayer(host, true);
    const persistent = options.persistent === true;
    const ttl = tone === 'error' ? 10000 : Math.max(1000, Number(ms) > 0 ? Number(ms) : 4000);
    const state = { id, node, tone, persistent, timer: 0, remaining: persistent ? -1 : ttl, deadline: Date.now() + ttl };
    activeToasts.set(id, state);
    if (!persistent) state.timer = setTimeout(() => removeToast(id), ttl);
    while (activeToasts.size > 3) {
      const oldest = Array.from(activeToasts.values()).find(item => !item.persistent) || activeToasts.values().next().value;
      if (!oldest) break;
      removeToast(oldest.id, true);
    }
    requestAnimationFrame(() => node.classList.add('is-visible'));
    return id;
  };
  window.ax2DismissToast = id => removeToast(Number(id), true);


  // ------------------------------------------------------------------
  // SECTION: Dialog layer tracking
  // Focus return, open state and closing.
  // ------------------------------------------------------------------
  /* Keep the dialog state discoverable to screen readers and keep the phone bottom navigation below sheets. */
  const modalSelector = '.mdl.open, .mdl.ax2-closing, #ux-command.open, #ux-activity.open, #ux-filter-drawer.open, #v54-job-detail.open, #video-move-dialog.open, #video-move-dialog[aria-hidden="false"], #ax-resume-dialog:not([hidden]), .ax2-sheet.open, .ax2-menu.open, #ax2-avatar-menu.open';
  const wasOpen = new WeakMap();
  const returnFocus = new WeakMap();
  const trackedLayers = new Set();
  const closingTimers = new WeakMap();
  let lastFocusTrigger = null;
  // Capture the initiating control before inline click handlers open a layer.
  document.addEventListener('click', event => {
    const trigger = event.target?.closest?.('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex]:not([tabindex="-1"])');
    if (trigger && !trigger.closest(modalSelector) && trigger.isConnected) lastFocusTrigger = trigger;
  }, true);
  // Some existing dialogs (notably resume playback) focus a field synchronously as they open.
  // Remember the previous focus target before the mutation observer runs.
  document.addEventListener('focusin', event => {
    const layer = topLayer();
    const previous = event.relatedTarget;
    if (layer && layer.contains(event.target) && previous && !layer.contains(previous) && !returnFocus.has(layer)) {
      returnFocus.set(layer, previous);
    }
  }, true);
  const dialogIds = new Map([
    ['ax-confirm', value => window.axConfirmClose?.(value)],
    ['pk', () => window.pkClose?.()], ['sk', () => window.skClose?.()], ['psmap', () => window.psMapClose?.()],
    ['vmr', () => window.vmrClose?.()], ['entity-edit', () => window.closeEntityEditor?.()],
    ['amr', () => window.amrClose?.()], ['suggest-preview', () => window.closeSuggestPreview?.()],
    ['sm', () => window.smClose?.()], ['mm', () => window.mmClose?.()],
    ['channel-edit-modal', () => window.closeChannelEditModal?.()],
    ['channel-shuffle-player', () => window.closeChannelShufflePlayerChoice?.()],
    ['video-quick-edit', () => window.closeVideoQuickEdit?.()],
    ['channel-duplicate-audit', () => window.channelDuplicateAuditClose?.()],
    ['perf-duplicate-audit', () => window.perfAuditClose?.()],
    ['video-move-dialog', () => window.closeVideoMoveDialog?.()],
    ['ux-command', () => window.uxCloseCommand?.()], ['ux-activity', () => window.uxCloseActivity?.()],
    ['ux-filter-drawer', () => window.uxCloseFilters?.()], ['v54-job-detail', () => window.closeJobDetails?.()],
    ['ax-resume-dialog', () => window.chooseAxResume?.('cancel')]
  ]);
  function getOpenLayers() {
    return $$(modalSelector).filter(el => {
      if (el.hidden) return false;
      if (el.matches('#video-move-dialog') && el.getAttribute('aria-hidden') === 'true' && !el.classList.contains('open')) return false;
      return getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
    }).sort((a, b) => (parseInt(getComputedStyle(a).zIndex, 10) || 0) - (parseInt(getComputedStyle(b).zIndex, 10) || 0));
  }
  function topLayer() { return getOpenLayers().at(-1) || null; }
  function focusable(root) {
    return $$('button:not(:disabled), [href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])', root)
      .filter(el => !el.hidden && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden');
  }
  function isActuallyOpen(el) { return !!el && !el.hidden && (el.classList.contains('open') || ((el.matches('#ax-resume-dialog, #video-move-dialog') || el.classList.contains('mdl')) && el.getAttribute('aria-hidden') === 'false')); }
  let layerStateFrame = 0;
  let observer = null;
  function updateLayerState() {
    // Disconnect while writing layer metadata so this observer never reacts to its own output.
    observer?.disconnect();
    try {
      const open = getOpenLayers();
      const bodyOpen = phone() && open.length > 0;
      if (document.body.classList.contains('ax2-layer-open') !== bodyOpen) document.body.classList.toggle('ax2-layer-open', bodyOpen);
      // Keep previously opened layers in the set after their `.open` class is removed,
      // otherwise the close transition/focus-return branch would never see them again.
      $$('.mdl').forEach(el => trackedLayers.add(el));
      $$(modalSelector).forEach(el => trackedLayers.add(el));
      trackedLayers.forEach(el => { if (!el.isConnected) { trackedLayers.delete(el); wasOpen.delete(el); returnFocus.delete(el); } });
      for (const el of trackedLayers) {
        const now = isActuallyOpen(el) && getComputedStyle(el).display !== 'none';
        const before = wasOpen.get(el) || false;
        if (now && !before) {
          const closingTimer = closingTimers.get(el);
          if (closingTimer) { clearTimeout(closingTimer); closingTimers.delete(el); }
          el.classList.remove('ax2-closing');
          if (!returnFocus.has(el)) {
            const active = document.activeElement;
            const candidate = active && active !== document.body && active !== document.documentElement && !el.contains(active)
              ? active
              : (lastFocusTrigger && lastFocusTrigger.isConnected && !el.contains(lastFocusTrigger) ? lastFocusTrigger : null);
            if (candidate) returnFocus.set(el, candidate);
          }
          if (el.classList.contains('mdl')) {
            setAttrIfChanged(el, 'aria-hidden', 'false');
            const box = el.querySelector(':scope > .mdl-box');
            if (box) {
              setAttrIfChanged(box, 'tabindex', '-1');
              if (!box.querySelector(':scope > .ax2-sheet-handle')) {
                const handle = document.createElement('span'); handle.className = 'ax2-sheet-handle'; handle.setAttribute('aria-hidden', 'true'); box.prepend(handle);
              }
            }
          }
          setTimeout(() => {
            if (!isActuallyOpen(el) || el.contains(document.activeElement)) return;
            const target = el.querySelector('[autofocus], input:not([type="hidden"]):not(:disabled), select:not(:disabled), textarea:not(:disabled), button:not(:disabled), [tabindex]:not([tabindex="-1"])');
            if (target && getComputedStyle(target).display !== 'none') target.focus({ preventScroll: true });
          }, 20);
        } else if (!now && before) {
          // Mark the modal closed before retaining its surface for the exit
          // animation. Without this, aria-hidden stays false while `.open` is
          // removed; any unrelated DOM mutation (for example a save toast)
          // can make isActuallyOpen() misclassify `.ax2-closing` as a reopen,
          // cancelling the animation and visibly flashing the dialog.
          if (el.classList.contains('mdl')) setAttrIfChanged(el, 'aria-hidden', 'true');
          const trigger = returnFocus.get(el);
          const closeDelay = el.classList.contains('mdl') && !el.hidden && !reduceMotion() ? 240 : 0;
          if (closeDelay) {
            el.classList.add('ax2-closing');
            const previousTimer = closingTimers.get(el);
            if (previousTimer) clearTimeout(previousTimer);
            const timer = setTimeout(() => {
              if (closingTimers.get(el) !== timer) return;
              closingTimers.delete(el);
              if (el.isConnected && !isActuallyOpen(el)) el.classList.remove('ax2-closing');
            }, closeDelay);
            closingTimers.set(el, timer);
          }
          if (trigger && trigger.isConnected) {
            setTimeout(() => {
              const visibleTop = topLayer();
              if (!visibleTop || visibleTop.contains(trigger)) { try { trigger.focus({ preventScroll: true }); } catch (_) {} }
            }, closeDelay);
          }
          returnFocus.delete(el);
        }
        wasOpen.set(el, now);
      }
    } finally {
      if (observer) observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'hidden', 'aria-hidden', 'style'] });
    }
  }
  function scheduleLayerState() {
    if (layerStateFrame) return;
    layerStateFrame = requestAnimationFrame(() => { layerStateFrame = 0; updateLayerState(); });
  }
  observer = new MutationObserver(scheduleLayerState);
  function observeLayers() {
    observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'hidden', 'aria-hidden', 'style'] });
    scheduleLayerState();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', observeLayers, { once: true }); else observeLayers();
  window.addEventListener('resize', scheduleLayerState, { passive: true });

  function closeLayer(el) {
    if (!el) return false;
    const close = dialogIds.get(el.id);
    if (close) { close(false); return true; }
    if (el.classList.contains('ax2-sheet')) { if (window.ax2?.closeSheet) window.ax2.closeSheet(); else el.classList.remove('open'); return true; }
    if (el.id === 'ax2-avatar-menu') { window.ax2?.closeMenu?.(); return true; }
    return false;
  }
  document.addEventListener('keydown', event => {
    const layer = topLayer();
    if (!layer) return;
    if (event.key === 'Escape') {
      if (layer.classList.contains('ax2-closing')) { event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation(); return; }
      if (layer.dataset.saving === 'true' || layer.querySelector('[data-saving="true"]')) {
        event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation(); return;
      }
      if (layer.id === 'ax-exit-overlay') return;
      if (closeLayer(layer)) { event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation(); }
      return;
    }
    if (event.key !== 'Tab' || layer.matches('.ax2-menu, #ax2-avatar-menu')) return;
    const items = focusable(layer);
    if (!items.length) { event.preventDefault(); layer.focus?.(); return; }
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || !layer.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || !layer.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
  }, true);
  document.addEventListener('click', event => {
    const layer = event.target?.closest?.('.mdl.open, #ux-command.open, #ux-activity.open, #ux-filter-drawer.open, #v54-job-detail.open, #video-move-dialog.open, #video-move-dialog[aria-hidden="false"], #ax-resume-dialog:not([hidden])');
    if (!layer || event.target !== layer) return;
    if (layer.dataset.saving === 'true' || layer.querySelector('[data-saving="true"]')) {
      event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation(); return;
    }
    closeLayer(layer);
  }, true);
  let swipeStart = null;
  document.addEventListener('pointerdown', event => {
    if (!phone()) return;
    const header = event.target?.closest?.('.mdl.open > .mdl-box > .mdl-h, .mdl.open > .mdl-box > .ax2-sheet-handle');
    if (header) swipeStart = { y: event.clientY, x: event.clientX, layer: header.closest('.mdl.open'), id: event.pointerId };
  }, { passive: true, capture: true });
  document.addEventListener('pointerup', event => {
    if (!swipeStart || event.pointerId !== swipeStart.id) return;
    const dy = event.clientY - swipeStart.y, dx = Math.abs(event.clientX - swipeStart.x), target = swipeStart.layer;
    swipeStart = null;
    if (target && dy > 80 && dy > dx * 1.2 && !target.dataset.saving && !target.querySelector('[data-saving="true"]')) closeLayer(target);
  }, { passive: true, capture: true });


  // ------------------------------------------------------------------
  // SECTION: Bio toggles
  // Expandable biography text.
  // ------------------------------------------------------------------
  /* Read-more controls appear only when the current biography actually exceeds three lines. */
  function syncBioToggles(root = document) {
    $$('[data-ax2-bio-toggle]', root).forEach(button => {
      const bio = document.getElementById(button.dataset.ax2BioToggle);
      if (!bio) { if (!button.hidden) button.hidden = true; return; }
      const expanded = bio.classList.contains('is-expanded');
      const overflowing = bio.scrollHeight > bio.clientHeight + 2;
      const shouldHide = !expanded && !overflowing;
      if (button.hidden !== shouldHide) button.hidden = shouldHide;
      const label = expanded ? 'Show less' : 'Read more';
      if (button.textContent !== label) button.textContent = label;
      const ariaExpanded = expanded ? 'true' : 'false';
      if (button.getAttribute('aria-expanded') !== ariaExpanded) button.setAttribute('aria-expanded', ariaExpanded);
    });
  }
  document.addEventListener('click', event => {
    const button = event.target?.closest?.('[data-ax2-bio-toggle]');
    if (!button) return;
    const bio = document.getElementById(button.dataset.ax2BioToggle);
    if (!bio) return;
    bio.classList.toggle('is-expanded'); syncBioToggles(button.parentElement);
  });
  let bioSyncFrame = 0;
  function scheduleBioSync() {
    if (bioSyncFrame) return;
    bioSyncFrame = requestAnimationFrame(() => { bioSyncFrame = 0; syncBioToggles(); });
  }
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(scheduleBioSync);
    window.addEventListener('load', () => { $$('.ax2-entity-bio').forEach(el => ro.observe(el)); scheduleBioSync(); }, { once: true });
  }
  const bioObserver = new MutationObserver(records => {
    const affectsBio = records.some(record => {
      const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      if (target?.closest?.('.ax2-entity-bio')) return true;
      return Array.from(record.addedNodes || []).some(node => node.nodeType === 1 && (node.matches('.ax2-entity-bio') || node.querySelector('.ax2-entity-bio')));
    });
    if (affectsBio) scheduleBioSync();
  });
  bioObserver.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  setTimeout(scheduleBioSync, 100);


  // ------------------------------------------------------------------
  // SECTION: Busy state wrappers
  // Wraps actions to show a busy state.
  // ------------------------------------------------------------------
  /* One busy-button helper; wrappers keep existing handlers and backend work untouched. */
  const ax2 = window.ax2 = window.ax2 || {};
  const roller = (size='sm', extraClass='') => window.AXLoadingFeedback?.rollerMarkup?.(size, extraClass)
    || `<span class="ax-lds-roller ${size}${extraClass?` ${extraClass}`:''}" aria-hidden="true">${'<i></i>'.repeat(8)}</span>`;
  ax2.busy = async function busy(button, promise, label = 'Working…') {
    if (!button) return await promise;
    if (button.dataset.ax2Busy === 'true') return;
    const originalHtml = button.innerHTML, originalDisabled = button.disabled, originalAria = button.getAttribute('aria-busy');
    const layer = button.closest('.mdl, #video-move-dialog, #ax-resume-dialog, #ux-command, #ux-activity, #ux-filter-drawer, #v54-job-detail');
    const originalSaving = layer ? layer.getAttribute('data-saving') : null;
    button.dataset.ax2Busy = 'true'; button.disabled = true; button.setAttribute('aria-busy', 'true');
    if (layer) layer.setAttribute('data-saving', 'true');
    button.innerHTML = `${roller('sm', 'ax2-busy-spinner')}<span>${String(label)}</span>`;
    try { return await promise; }
    finally {
      button.innerHTML = originalHtml;
      button.disabled = originalDisabled;
      if (originalAria === null) button.removeAttribute('aria-busy'); else button.setAttribute('aria-busy', originalAria);
      delete button.dataset.ax2Busy;
      if (layer) {
        if (originalSaving === null) layer.removeAttribute('data-saving');
        else layer.setAttribute('data-saving', originalSaving);
      }
    }
  };
  function wrapBusy(name, buttonGetter, label) {
    const original = window[name];
    if (typeof original !== 'function' || original.__ax2BusyWrapped) return;
    const wrapper = function (...args) {
      // Some wrapped handlers are invoked by an inline form submit handler.
      // Their original function calls preventDefault(), but this wrapper defers
      // that function to a microtask so the busy state can be painted first.
      // By then the browser has already decided whether to perform a native
      // form submission, which can reload the page and make the dialog flash.
      const eventArg = args[0];
      const isSubmitEvent = !!(eventArg && eventArg.type === 'submit' && typeof eventArg.preventDefault === 'function');
      if (isSubmitEvent) eventArg.preventDefault();

      const button = typeof buttonGetter === 'function' ? buttonGetter(args, this) : $(buttonGetter);
      if (button?.dataset.ax2Busy === 'true') return isSubmitEvent ? false : undefined;

      const running = ax2.busy(button, Promise.resolve().then(() => original.apply(this, args)), label);
      if (isSubmitEvent) {
        // Inline `onsubmit` needs a synchronous false return. The task has
        // already been started above; handle its rejection here because the
        // inline event handler cannot await its promise.
        running.catch(error => {
          console.error(`[Archive X] ${name} failed`, error);
          window.toast?.(`Save failed: ${String(error?.message || error || 'unknown error')}`);
        });
        return false;
      }
      return running;
    };
    wrapper.__ax2BusyWrapped = true; wrapper.__ax2Original = original; window[name] = wrapper;
  }
  function bindBusyWrappers() {
    wrapBusy('saveVideoQuickEdit', () => $('#vq-save'), 'Saving…');
    wrapBusy('saveEntityEditor', () => $('#entity-edit-save'), 'Saving…');
    wrapBusy('saveChannelCardEdit', () => $('#channel-edit-save'), 'Saving…');
    wrapBusy('saveAccountProfileEdit', () => $('#account-edit-save'), 'Saving…');
    wrapBusy('psMapSave', () => $('#psmap-save'), 'Saving…');
    wrapBusy('vmrSave', () => $('#vmr .btn.pri'), 'Saving…');
    wrapBusy('smSend', () => $('#sm .btn.pri'), 'Sending…');
    wrapBusy('confirmVideoMoveDelete', () => $('#video-move-confirm'), 'Moving…');
  }
  bindBusyWrappers();
  window.addEventListener('load', bindBusyWrappers, { once: true });


  // ------------------------------------------------------------------
  // SECTION: Connection banner
  // Live socket state and offline banner.
  // ------------------------------------------------------------------
  /* Keep the persistent socket outage separate from short-lived toast notices. */
  let socketDownSince = 0, connectionBannerShown = false, socketEverOpened = false, socketAttemptSeen = false;
  function retryOperationsSocket() {
    try { if (typeof retryOpsSocket === 'function') retryOpsSocket(); } catch (_) {}
  }
  function syncConnectionBannerOffset() {
    const banner = $('#ax2-connection-banner');
    const height = banner && !banner.hidden ? banner.offsetHeight : 0;
    document.documentElement.style.setProperty('--ax-connection-banner-height', `${height}px`);
  }
  function ensureConnectionBanner() {
    let banner = $('#ax2-connection-banner');
    if (banner) return banner;
    banner = document.createElement('div');
    banner.id = 'ax2-connection-banner';
    banner.hidden = true;
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    const spinner = document.createElement('span');
    spinner.className = 'ax2-connection-spin';
    spinner.setAttribute('aria-hidden', 'true');
    spinner.innerHTML = roller('xs');
    const message = document.createElement('span');
    message.className = 'ax2-connection-message';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Retry now';
    retry.addEventListener('click', () => {
      retryOperationsSocket();
      retry.disabled = true;
      retry.textContent = 'Retrying…';
      setTimeout(() => {
        if (retry.isConnected) { retry.disabled = false; retry.textContent = 'Retry now'; }
      }, 1500);
    });
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'ax2-connection-close';
    close.setAttribute('aria-label', 'Dismiss connection warning');
    close.innerHTML = icon('close');
    close.addEventListener('click', () => { banner.hidden = true; syncConnectionBannerOffset(); });
    banner.append(spinner, message, retry, close);
    const topbar = $('#topbar');
    if (topbar?.parentNode) topbar.parentNode.insertBefore(banner, topbar.nextSibling);
    else document.body.appendChild(banner);
    if (typeof ResizeObserver === 'function') new ResizeObserver(syncConnectionBannerOffset).observe(banner);
    return banner;
  }
  function setOfflinePill(forceOffline) {
    const pill = $('[data-ax2-tg-pill]');
    if (!pill) return;
    setAttrIfChanged(pill, 'data-ax2-ops-offline', forceOffline ? 'true' : 'false');
    window.ax2?.syncMirrors?.();
  }
  function socketState() {
    try {
      if (typeof _opsSocket === 'undefined' || !_opsSocket) return 'missing';
      socketAttemptSeen = true;
      if (_opsSocket.readyState === WebSocket.OPEN && (typeof opsSocketHealthy !== 'function' || opsSocketHealthy())) { socketEverOpened = true; return 'open'; }
      if (_opsSocket.readyState === WebSocket.CONNECTING) return 'connecting';
      return 'closed';
    } catch (_) { return 'missing'; }
  }
  function checkSocket() {
    if (typeof _opsLiveEnabled !== 'undefined' && !_opsLiveEnabled) {
      socketDownSince = 0; socketEverOpened = false; socketAttemptSeen = false;
      connectionBannerShown = false;
      const banner = $('#ax2-connection-banner');
      if (banner) banner.hidden = true;
      syncConnectionBannerOffset();
      setOfflinePill(false);
      return;
    }
    // A fast rejected handshake may close between monitor ticks. The live
    // controller still knows it attempted a connection.
    if (typeof _opsLiveEnabled !== 'undefined' && _opsLiveEnabled) socketAttemptSeen = true;
    const state = socketState();
    // A first handshake also needs a deadline; a stuck one is an outage.
    if (state === 'missing' && !socketAttemptSeen && !socketEverOpened) return;
    if (state === 'open') {
      if (socketDownSince) {
        socketDownSince = 0;
        if (connectionBannerShown) {
          connectionBannerShown = false;
          setOfflinePill(false);
          const banner = $('#ax2-connection-banner');
          if (banner) banner.hidden = true;
          syncConnectionBannerOffset();
          window.toast?.('Reconnected to Archive X', 4000, { tone: 'success' });
        }
      }
      return;
    }
    if (!socketDownSince) socketDownSince = Date.now();
    const duration = Date.now() - socketDownSince;
    if (duration < 5000) return;
    const banner = ensureConnectionBanner();
    if (!connectionBannerShown) {
      connectionBannerShown = true;
      setOfflinePill(true);
      banner.hidden = false;
    }
    let retry = 0;
    try { retry = Number(typeof _opsSocketRetry !== 'undefined' ? _opsSocketRetry : 0) || 0; } catch (_) {}
    const message = banner.querySelector('.ax2-connection-message');
    if (message) setTextIfChanged(message, `Lost connection to Archive X — reconnecting (attempt ${Math.max(1, retry + 1)})…`);
    syncConnectionBannerOffset();
  }
  setInterval(checkSocket, 2000);


  // ------------------------------------------------------------------
  // SECTION: File input names
  // Shows chosen filenames on file inputs.
  // ------------------------------------------------------------------
  /* Native file input remains present; the existing visible button opens it. Show the chosen filename nearby. */
  function bindFileNames() {
    const specs = [
      ['entity-edit-image-file', 'entity-edit-image-name'],
      ['channel-edit-photo-input', 'channel-edit-photo-name']
    ];
    specs.forEach(([inputId, labelId]) => {
      const input = document.getElementById(inputId); if (!input || input.dataset.ax2FileBound) return;
      input.dataset.ax2FileBound = '1';
      let meta = document.getElementById(labelId);
      if (!meta) { meta = document.createElement('span'); meta.id = labelId; meta.className = 'ax2-file-name'; meta.textContent = 'No file selected'; input.insertAdjacentElement('afterend', meta); }
      input.addEventListener('change', () => { meta.textContent = input.files?.[0]?.name || 'No file selected'; });
    });
  }
  bindFileNames();
  let fileNameFrame = 0;
  const containsFileInput = node => node && node.nodeType === 1 && (node.matches('input[type="file"]') || !!node.querySelector('input[type="file"]'));
  new MutationObserver(records => {
    const hasFileInput = records.some(record => record.type === 'childList' && Array.from(record.addedNodes).some(containsFileInput));
    if (!hasFileInput || fileNameFrame) return;
    fileNameFrame = requestAnimationFrame(() => { fileNameFrame = 0; bindFileNames(); });
  }).observe(document.documentElement, { childList: true, subtree: true });


})();
