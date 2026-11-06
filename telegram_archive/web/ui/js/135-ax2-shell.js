/* ============================================================================
 * FILE: js/135-ax2-shell.js
 * AREA: Shell and navigation
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - State and layer transitions
 *   - Scroll lock and layer stack
 *   - Menus and sheets
 *   - Mirrored UI state
 *   - Responsive header
 *   - View decoration
 *   - Public API and init
 * ============================================================================ */
/* AX2 shell runtime — presentation only. Wraps existing navigation; all actions still call Archive X's existing handlers. */
(function () {
  'use strict';
  if (window.ax2 && window.ax2.__initialized) return;


  // ------------------------------------------------------------------
  // SECTION: State and layer transitions
  // Helpers, state and overlay transition bookkeeping.
  // ------------------------------------------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const state = { layer: null, trigger: null, bodyScrollY: 0, bodyLock: null, lastScrollY: window.scrollY || 0, closingTimer: 0, scrimFrame: 0, scrimToken: 0, layerGeneration: 0 };
  const layerTransitionTokens = new WeakMap();
  const layerCloseTimers = new WeakMap();
  function beginLayerTransition(layer) {
    if (!layer) return 0;
    const timer = layerCloseTimers.get(layer);
    if (timer) { window.clearTimeout(timer); layerCloseTimers.delete(layer); }
    const token = (layerTransitionTokens.get(layer) || 0) + 1;
    layerTransitionTokens.set(layer, token);
    return token;
  }
  function cancelScrimTransition() {
    if (state.scrimFrame) window.cancelAnimationFrame(state.scrimFrame);
    state.scrimFrame = 0;
    if (state.closingTimer) window.clearTimeout(state.closingTimer);
    state.closingTimer = 0;
    state.scrimToken++;
  }
  const reduceMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const icon = (id) => `<svg class="ax-icon" aria-hidden="true"><use href="#ax-${id}"></use></svg>`;

  // ------------------------------------------------------------------
  // SECTION: Scroll lock and layer stack
  // Open classes, focus targets, scrims and closing layers.
  // ------------------------------------------------------------------
  function syncLayerOpenClass() {
    const phone = (syncLayerOpenClass._mq || (syncLayerOpenClass._mq = window.matchMedia('(max-width:599px), (max-height:500px) and (orientation:landscape)'))).matches;
    const resume = $('#ax-resume-dialog');
    const resumeOpen = !!(resume && resume.getAttribute('aria-hidden') !== 'true' && getComputedStyle(resume).display !== 'none');
    const open = !!document.querySelector('.mdl.open, .ax2-sheet.open, .ax2-sheet.closing, .ax2-menu.open, #ux-command.open, #ux-activity.open, #ux-filter-drawer.open, #v54-job-detail.open, #video-move-dialog.open') || resumeOpen;
    document.body.classList.toggle('ax2-layer-open', phone && open);
  }
  function syncTelegramStatus() {
    // #tglabel and #tgdot are owned by 09-app.js. This shell reads them only;
    // its visible copy is separate so the status observer cannot observe its own writes.
    const sourceLabel = $('#tglabel'), sourceDot = $('#tgdot'), pill = $('[data-ax2-tg-pill]');
    if (!sourceLabel || !sourceDot) return { state: 'connecting', raw: 'Connecting…', label: 'Connecting…' };
    const raw = (sourceLabel.dataset.ax2RawStatus || sourceLabel.textContent || sourceLabel.title || 'Connecting…').trim();
    const low = raw.toLowerCase();
    let state = 'offline', short = 'Offline';
    if (/api.?id|api.?hash|credential|required|setup.?needed|needs.?setup|log.?in|not authorized|not logged/.test(low)) { state = 'setup'; short = 'Setup needed'; }
    else if (/connect(?:ing|ion)?|reconnect|starting|initiali[sz]ing/.test(low) && !/connected/.test(low)) { state = 'connecting'; short = 'Connecting…'; }
    else if (/connected|authenticated|session saved/.test(low) && !/disconnected|not connected/.test(low)) { state = 'connected'; short = 'Connected'; }
    else if (/offline|disconnect|error|failed|unavailable|not connected/.test(low)) { state = 'offline'; short = 'Offline'; }
    else if (!sourceDot.classList.contains('off')) { state = 'connected'; short = 'Connected'; }

    const forcedOffline = !!pill && pill.getAttribute('data-ax2-ops-offline') === 'true';
    const title = forcedOffline ? 'Operations connection lost' : raw;
    if (forcedOffline) { state = 'offline'; short = 'Offline'; }

    const displayText = $('[data-ax2-tg-text]');
    const displayDot = $('[data-ax2-tg-dot]');
    writeText(displayText, short);
    toggleClass(displayDot, 'off', state !== 'connected');
    toggleClass(displayDot, 'ax2-tg-warn', state === 'setup');
    toggleClass(displayDot, 'ax2-tg-bad', state === 'offline');
    if (pill) { setAttr(pill, 'data-ax2-telegram-state', state); setTitle(pill, title); }
    const menuCopy = $('[data-ax2-tg-copy]');
    if (menuCopy) {
      writeText(menuCopy, short); setTitle(menuCopy, title);
      setAttr(menuCopy, 'data-ax2-telegram-state', state);
      toggleClass(menuCopy, 'off', state !== 'connected');
    }
    const avatarStatus = $('[data-ax2-avatar-status]');
    if (avatarStatus) {
      toggleClass(avatarStatus, 'is-connected', state === 'connected');
      toggleClass(avatarStatus, 'is-warn', state === 'setup');
      toggleClass(avatarStatus, 'is-bad', state === 'offline');
      setTitle(avatarStatus, short);
    }
    return { state, raw, label: short };
  }

  function setExpanded(trigger, expanded) {
    if (trigger) trigger.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  }
  function lockScroll() {
    if (state.bodyLock) return;
    state.bodyScrollY = window.scrollY || window.pageYOffset || 0;
    const body = document.body;
    state.bodyLock = { position: body.style.position, top: body.style.top, width: body.style.width, overflow: body.style.overflow };
    body.style.position = 'fixed';
    body.style.top = `-${state.bodyScrollY}px`;
    body.style.width = '100%';
    body.style.overflow = 'hidden';
  }
  function unlockScroll() {
    if (!state.bodyLock) return;
    const body = document.body;
    const saved = state.bodyLock;
    body.style.position = saved.position;
    body.style.top = saved.top;
    body.style.width = saved.width;
    body.style.overflow = saved.overflow;
    state.bodyLock = null;
    window.scrollTo(0, state.bodyScrollY);
  }
  function focusables(el) {
    return $$('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"]),[role="menuitem"]', el)
      .filter(node => !node.hidden && node.getAttribute('aria-hidden') !== 'true' && node.getClientRects().length);
  }
  function topLayer() { return state.layer; }
  function raiseScrim() {
    const scrim = $('#ax2-layer-scrim');
    if (!scrim) return;
    if (state.closingTimer) window.clearTimeout(state.closingTimer);
    state.closingTimer = 0;
    if (state.scrimFrame) window.cancelAnimationFrame(state.scrimFrame);
    const token = ++state.scrimToken;
    scrim.hidden = false;
    scrim.setAttribute('aria-hidden', 'false');
    state.scrimFrame = requestAnimationFrame(() => {
      state.scrimFrame = 0;
      if (token !== state.scrimToken || !state.layer || scrim.hidden || scrim.getAttribute('aria-hidden') === 'true') return;
      scrim.classList.add('open');
    });
  }
  function lowerScrim() {
    const scrim = $('#ax2-layer-scrim');
    if (!scrim) return;
    if (state.scrimFrame) window.cancelAnimationFrame(state.scrimFrame);
    state.scrimFrame = 0;
    if (state.closingTimer) window.clearTimeout(state.closingTimer);
    const token = ++state.scrimToken;
    scrim.classList.remove('open');
    scrim.setAttribute('aria-hidden', 'true');
    state.closingTimer = window.setTimeout(() => {
      if (token !== state.scrimToken) return;
      state.closingTimer = 0;
      if (!state.layer && !scrim.classList.contains('open')) scrim.hidden = true;
    }, reduceMotion() ? 1 : 270);
  }
  function closeMenu(menu) {
    menu = menu || $('#ax2-avatar-menu');
    if (!menu) return;
    beginLayerTransition(menu);
    state.layerGeneration++;
    menu.classList.remove('open');
    menu.setAttribute('aria-hidden', 'true');
    menu.hidden = true;
    setExpanded($('#ax2-avatar-button'), false);
    if (state.layer === menu) state.layer = null;
    if (state.trigger === $('#ax2-avatar-button')) state.trigger = null;
    lowerScrim();
    syncLayerOpenClass();
    syncHeaderVisibility(true);
  }
  function closeSheet(sheet) {
    sheet = sheet || (state.layer && state.layer.matches && state.layer.matches('.ax2-sheet') ? state.layer : null);
    if (!sheet) return;
    const token = beginLayerTransition(sheet);
    const generation = ++state.layerGeneration;
    sheet.classList.add('closing');
    sheet.classList.remove('open');
    sheet.setAttribute('aria-hidden', 'true');
    setExpanded(state.trigger, false);
    if (state.layer === sheet) state.layer = null;
    const trigger = state.trigger;
    state.trigger = null;
    unlockScroll();
    lowerScrim();
    syncLayerOpenClass();
    const wait = reduceMotion() ? 1 : 250;
    const timer = window.setTimeout(() => {
      if (layerCloseTimers.get(sheet) !== timer) return;
      layerCloseTimers.delete(sheet);
      if (layerTransitionTokens.get(sheet) !== token || state.layer === sheet || sheet.classList.contains('open') || sheet.getAttribute('aria-hidden') !== 'true') return;
      sheet.hidden = true;
      sheet.classList.remove('closing');
      syncLayerOpenClass();
      if (generation === state.layerGeneration && !state.layer && trigger && trigger.isConnected) trigger.focus({ preventScroll: true });
    }, wait);
    layerCloseTimers.set(sheet, timer);
    syncHeaderVisibility(true);
  }
  function clearOrphanedScrim() {
    const scrim = $('#ax2-layer-scrim');
    if (!scrim || (scrim.hidden && !scrim.classList.contains('open') && scrim.getAttribute('aria-hidden') !== 'false')) return false;
    cancelScrimTransition();
    scrim.classList.remove('open', 'closing');
    scrim.setAttribute('aria-hidden', 'true');
    scrim.hidden = true;
    state.trigger?.setAttribute('aria-expanded', 'false');
    state.trigger = null;
    unlockScroll();
    syncLayerOpenClass();
    syncHeaderVisibility(true);
    return true;
  }
  function closeTopLayer(options) {
    options = options || {};
    const immediate = options?.immediate === true;
    const layer = topLayer();
    if (immediate && layer) {
      beginLayerTransition(layer);
      state.layerGeneration++;
      layer.classList.remove('open', 'closing');
      layer.hidden = true;
      layer.setAttribute('aria-hidden', 'true');
      state.trigger?.setAttribute('aria-expanded', 'false');
      state.layer = null;
      state.trigger = null;
      const scrim = $('#ax2-layer-scrim');
      if (scrim) {
        cancelScrimTransition();
        scrim.classList.remove('open', 'closing');
        scrim.setAttribute('aria-hidden', 'true');
        scrim.hidden = true;
      }
      unlockScroll();
      syncLayerOpenClass();
      syncHeaderVisibility(true);
      return true;
    }
    // Bulk-edit popovers can be opened after navigation has already detached
    // the active layer. In that state the shared scrim must still be cleaned up;
    // otherwise the portalled scrim remains above the app and dims the menu.
    if (!layer) return clearOrphanedScrim();
    if (layer.matches('.ax2-menu')) closeMenu(layer);
    else closeSheet(layer);
    return true;
  }
  function closeAnyLayer() { if (state.layer) closeTopLayer(); else clearOrphanedScrim(); }


  // ------------------------------------------------------------------
  // SECTION: Menus and sheets
  // Positioning and opening.
  // ------------------------------------------------------------------
  function positionMenu(menu, trigger) {
    if (!menu || !trigger || !trigger.isConnected) return;
    const rect = trigger.getBoundingClientRect();
    // A hidden/unlaid-out trigger can occur during startup or while its shell is hidden.
    // Keep the CSS fallback placement in that case; a zero rect is not a viewport anchor.
    if (rect.width <= 0 || rect.height <= 0) {
      menu.style.removeProperty('left');
      menu.style.removeProperty('top');
      menu.style.removeProperty('right');
      return;
    }
    const menuHeight = Math.min(menu.offsetHeight || 420, Math.max(160, window.innerHeight - 16));
    const top = Math.max(8, Math.min(window.innerHeight - menuHeight - 8, rect.bottom + 6));
    const right = Math.max(10, window.innerWidth - rect.right);
    // Late legacy parity CSS marks the account menu's default right edge important.
    // Use inline important coordinates so the portalled menu follows the actual trigger
    // in viewport space instead of snapping back to the fixed shell margin.
    menu.style.setProperty('left', 'auto', 'important');
    menu.style.setProperty('top', `${top}px`, 'important');
    menu.style.setProperty('right', `${right}px`, 'important');
  }
  function positionSheetPopover(sheet, trigger) {
    if (!sheet) return;
    const popover = window.innerWidth >= 600;
    sheet.classList.toggle('ax2-popover', popover);
    if (popover && trigger && trigger.isConnected) {
      const rect = trigger.getBoundingClientRect();
      const top = Math.max(8, Math.min(window.innerHeight - 80, rect.bottom + 8));
      sheet.style.top = `${top}px`;
      sheet.style.right = `${Math.max(12, window.innerWidth - rect.right)}px`;
      sheet.style.left = 'auto';
    } else {
      sheet.style.top = '';
      sheet.style.right = '';
      sheet.style.left = '';
    }
  }

  function openMenu(menu, trigger) {
    menu = menu || $('#ax2-avatar-menu');
    trigger = trigger || $('#ax2-avatar-button');
    if (!menu) return;
    if (state.layer && state.layer !== menu) closeTopLayer();
    if (menu.classList.contains('open')) { closeMenu(menu); return; }
    const token = beginLayerTransition(menu);
    menu.hidden = false;
    menu.setAttribute('aria-hidden', 'false');
    menu.classList.remove('closing');
    positionMenu(menu, trigger);
    state.layer = menu;
    state.layerGeneration++;
    state.trigger = trigger;
    setExpanded(trigger, true);
    raiseScrim();
    syncLayerOpenClass();
    syncHeaderVisibility(false);
    requestAnimationFrame(() => {
      if (layerTransitionTokens.get(menu) !== token || state.layer !== menu || menu.hidden || menu.getAttribute('aria-hidden') === 'true') return;
      menu.classList.add('open');
      positionMenu(menu, trigger);
      syncLayerOpenClass();
      const first = focusables(menu)[0];
      if (first) first.focus({ preventScroll: true });
    });
  }
  function openSheet(sheet, trigger) {
    sheet = sheet || $('#ax2-more-layer');
    if (!sheet) return;
    if (state.layer && state.layer !== sheet) closeTopLayer();
    if (sheet.classList.contains('open')) { closeSheet(sheet); return; }
    const token = beginLayerTransition(sheet);
    sheet.hidden = false;
    sheet.setAttribute('aria-hidden', 'false');
    sheet.classList.remove('closing');
    state.layer = sheet;
    state.layerGeneration++;
    state.trigger = trigger || null;
    if (trigger) setExpanded(trigger, true);
    const popover = window.innerWidth >= 600;
    positionSheetPopover(sheet, trigger);
    if (!popover) lockScroll();
    raiseScrim();
    syncLayerOpenClass();
    syncHeaderVisibility(false);
    requestAnimationFrame(() => {
      if (layerTransitionTokens.get(sheet) !== token || state.layer !== sheet || sheet.hidden || sheet.getAttribute('aria-hidden') === 'true') return;
      sheet.classList.add('open');
      syncLayerOpenClass();
      const first = focusables(sheet)[0];
      if (first) first.focus({ preventScroll: true });
    });
  }

  // ------------------------------------------------------------------
  // SECTION: Mirrored UI state
  // Copies existing app state into the shell chrome.
  // ------------------------------------------------------------------
  function syncIndicators(viewOverride) {
    const active = document.querySelector('#view-stage > .view.on, .view.on');
    let view = viewOverride;
    if (!view && active) {
      const map = { vl: 'library', vphotos: 'photos', vp: 'performers', vsite: 'sites', va: 'admin', vs: 'settings', vtransfers: 'transfers', vai: 'ai', dtop: 'player', dmain: 'player' };
      view = map[active.id] || active.dataset.view || 'library';
    }
    if (view === 'home' || view === 'tag' || view === 'player') view = 'library';
    const sidebarKey = view === 'sites' ? 'sites' : view;
    const sideLookup = view === 'ai' ? 'ai' : (sidebarKey || 'library');
    const side = $(`#ax-sidebar .ax-side-item[data-side="${CSS.escape(sideLookup)}"]`) || $(`#ax-sidebar .ax-side-item[data-side="library"]`);
    const sideIndicator = $('.ax2-side-indicator');
    if (side && sideIndicator) {
      const rail = window.matchMedia('(min-width:600px) and (max-width:999px), (orientation:landscape) and (max-height:500px)').matches;
      const railOffset = side.offsetTop + (side.offsetHeight - (rail ? 30 : 44)) / 2;
      sideIndicator.style.setProperty('--ax2-indicator-y', `${Math.max(0, railOffset)}px`);
      sideIndicator.style.opacity = '1';
      $$('#ax-sidebar .ax-side-item').forEach(el => el.classList.toggle('on', el === side));
    }
    const bottomMap = { library: 'library', photos: 'photos', performers: 'performers', sites: 'sites', transfers: 'transfers' };
    const activeBottom = bottomMap[view] || 'more';
    $$('.ax-ux-bottom-nav [data-ux-bottom]').forEach(btn => btn.classList.toggle('on', btn.dataset.uxBottom === activeBottom));
    document.body.classList.toggle('ax2-player-view', view === 'player' || !!$('#do.open'));
  }
  function writeText(node, value) { if (node && node.textContent !== String(value)) node.textContent = String(value); }
  function setAttr(node, name, value) { const next = String(value); if (node && node.getAttribute(name) !== next) node.setAttribute(name, next); }
  function setTitle(node, value) { setAttr(node, 'title', value); }
  function toggleClass(node, cls, on) { const wanted = !!on; if (node && node.classList.contains(cls) !== wanted) node.classList.toggle(cls, wanted); }
  function setIcon(useEl, id) { const href = `#ax-${id}`; if (useEl && useEl.getAttribute('href') !== href) useEl.setAttribute('href', href); }
  function setHidden(node, value) { if (node && node.hidden !== !!value) node.hidden = !!value; }
  let mirrorSyncPending = false;
  function syncMirrors() {
    if (mirrorSyncPending) return;
    mirrorSyncPending = true;
    window.requestAnimationFrame(() => { mirrorSyncPending = false; renderMirrors(); });
  }
  function renderMirrors() {
    const running = $('#ops-running-count');
    const queued = $('#ops-queued-count') || $('#ops-queue-count');
    const runValue = running ? (running.textContent || '').trim() : '0';
    const queueValue = queued ? (queued.textContent || '').trim() : '0';
    const opsBadge = $('[data-ax2-ops-badge]');
    if (opsBadge) { writeText(opsBadge, runValue || '0'); setHidden(opsBadge, !(parseInt(runValue, 10) > 0)); }
    const summary = $('[data-ax2-ops-summary]');
    writeText(summary, `${runValue || '0'} running · ${queueValue || '0'} queued`);
    const telegram = syncTelegramStatus();
    const connected = telegram.state === 'connected';
    const status = telegram.label;
    const pct = ($('#ax-storage-pct')?.textContent || '—').trim();
    $$('[data-ax2-storage-copy]').forEach(el => writeText(el, /used\s*$/i.test(pct) ? pct : `${pct} used`));
    const data = ($('#ax-storage-data')?.textContent || '').trim();
    const free = ($('#ax-storage-free')?.textContent || '').trim();
    const summaryText = [data, free].filter(Boolean).join(' · ');
    $$('[data-ax2-storage-data]').forEach(el => writeText(el, summaryText || 'Storage information unavailable'));
    const fill = $('#ax-storage-fill');
    const bar = $('#ax-storage-bar');
    const pctNumber = parseFloat(pct);
    const storageWidth = `${Number.isFinite(pctNumber) ? Math.max(0, Math.min(100, pctNumber)) : 0}%`;
    $$('[data-ax2-storage-fill]').forEach(el => { if (el.style.getPropertyValue('--ax2-storage-width') !== storageWidth) el.style.setProperty('--ax2-storage-width', storageWidth); });
    const themeButton = $('#ax2-theme-button');
    const themeMenu = $('#ax2-avatar-menu .ax2-menu-item[role="switch"]');
    const light = document.documentElement.getAttribute('data-theme-mode') === 'light' || document.documentElement.getAttribute('data-theme') === 'light';
    if (themeButton) setAttr(themeButton, 'aria-pressed', light ? 'true' : 'false');
    if (themeMenu) setAttr(themeMenu, 'aria-checked', light ? 'false' : 'true');
    const menuIcon = $('[data-ax2-theme-menu-icon] use');
    setIcon(menuIcon, light ? 'sun' : 'moon');
    const avatar = $('[data-ax2-avatar-initial]');
    const avatarName = $('[data-ax2-user-name]');
    let name = ($('#dashboard-user-name')?.textContent || $('[data-dashboard-user]')?.getAttribute('data-dashboard-user') || '').trim();
    if (window.axAccountProfile && window.axAccountProfile.name) name = window.axAccountProfile.name;
    if (!name) name = 'A';
    if (avatar) writeText(avatar, name.slice(0, 1).toUpperCase());
    if (avatarName) writeText(avatarName, name);
    syncAvatarPhoto();
  }
  let avatarPhotoTried = 0, avatarPhotoOk = false;
  function syncAvatarPhoto() {
    const holder = $('#ax2-avatar-button');
    if (!holder || avatarPhotoOk) return;
    const now = Date.now();
    if (now - avatarPhotoTried < 60000) return;
    avatarPhotoTried = now;
    const probe = new Image();
    probe.onload = () => {
      avatarPhotoOk = true;
      let img = holder.querySelector('img.ax2-avatar-photo');
      if (!img) { img = document.createElement('img'); img.className = 'ax2-avatar-photo'; img.alt = ''; holder.insertBefore(img, holder.firstChild); }
      img.src = probe.src;
      holder.classList.add('has-photo');
    };
    probe.src = (window.axAccountProfile && window.axAccountProfile.hasImage ? '/api/account/profile/image?v=' + window.axAccountProfile.version : '/api/telegram/me/photo?v=' + now);
  }
  window.axRefreshAvatarPhoto = function () {
    avatarPhotoOk = false; avatarPhotoTried = 0;
    const holder = $('#ax2-avatar-button');
    const img = holder && holder.querySelector('img.ax2-avatar-photo');
    if (img) img.remove();
    if (holder) holder.classList.remove('has-photo');
    syncAvatarPhoto();
  };

  // ------------------------------------------------------------------
  // SECTION: Responsive header
  // Labels, visibility and scroll behaviour.
  // ------------------------------------------------------------------
  function syncResponsiveLabels() {
    const search = $('#gs');
    if (search) {
      const phone = window.innerWidth < 600;
      const photos = search.dataset.searchContext === 'photos';
      const nextPlaceholder = photos ? 'Search photos…' : phone ? 'Search…' : 'Search videos, performers, studios…';
      if (search.getAttribute('placeholder') !== nextPlaceholder) search.setAttribute('placeholder', nextPlaceholder);
    }
  }
  function syncHeaderVisibility(forceShow) {
    const topbar = $('#topbar');
    if (!topbar) return;
    const hdr = $('#hdr');
    topbar.classList.toggle('is-scrolled', window.scrollY > 4);
    const compact = window.matchMedia('(max-width:599px), (max-height:500px) and (orientation:landscape)').matches;
    const preventHide = state.layer || $('#gs') === document.activeElement || hdr?.contains(document.activeElement);
    if (forceShow || !compact || preventHide) topbar.classList.remove('ax2-header-hidden');
  }
  const compactMQ = window.matchMedia('(max-width:599px), (max-height:500px) and (orientation:landscape)');
  let scrollTick = 0;
  function onScroll() {
    if (scrollTick) return;
    scrollTick = requestAnimationFrame(() => { scrollTick = 0; onScrollNow(); });
  }
  function onScrollNow() {
    const y = window.scrollY || 0;
    const topbar = $('#topbar');
    if (!topbar) return;
    topbar.classList.toggle('is-scrolled', y > 4);
    const compact = compactMQ.matches;
    const preventHide = !!state.layer || $('#gs') === document.activeElement || $('#hdr')?.contains(document.activeElement);
    if (!compact || preventHide) topbar.classList.remove('ax2-header-hidden');
    else if (y > state.lastScrollY && y > 120) topbar.classList.add('ax2-header-hidden');
    else if (y < state.lastScrollY) topbar.classList.remove('ax2-header-hidden');
    state.lastScrollY = y;
  }

  // ------------------------------------------------------------------
  // SECTION: View decoration
  // Image and heading marks, view animation and file inputs.
  // ------------------------------------------------------------------
  function markImages(root = document) {
    $$('img', root).forEach(img => {
      if (img.complete && img.naturalWidth > 0) {
        img.classList.remove('ax2-image-pending');
        img.classList.add('is-loaded');
      }
      else img.classList.add('ax2-image-pending');
    });
  }
  function markHeadings(root = document) {
    $$('.library-head, .ax-ops-page-intro, .ax-transfer-page-intro, .ai-page-head', root).forEach(el => el.classList.add('ax2-page-head'));
  }
  function animateView(viewOverride) {
    const view = viewOverride || $('#view-stage > .view.on') || $('.view.on');
    document.documentElement.classList.add('ax2-view-motion');
    if (!view) return;
    markImages(view);
    markHeadings(view);
    syncIndicators();
    syncMirrors();
    // Entrance ownership lives in the shared motion controller. Repeated shell
    // syncs must not restart an entrance or leave a delayed cleanup behind.
    window.AXMotion?.sync();
  }
  function initFileInputNames(root = document) {
    $$( 'input[type="file"]', root).forEach(input => {
      if (input.dataset.ax2FileNameBound === '1') return;
      input.classList.add('ax2-native-file-input');
      input.dataset.ax2FileNameBound = '1';
      const id = input.id || 'file';
      let note = input.parentElement?.querySelector(`[data-ax2-file-name-for="${CSS.escape(id)}"]`);
      if (!note) {
        note = document.createElement('span');
        note.className = 'ax2-file-name';
        note.dataset.ax2FileNameFor = id;
        if (input.id === 'channel-edit-photo-input') {
          const controls = input.closest('.channel-edit-photo-controls');
          (controls?.querySelector('.channel-edit-photo-buttons') || input).insertAdjacentElement('afterend', note);
        } else input.insertAdjacentElement('afterend', note);
      }
      const update = () => { note.textContent = input.files && input.files.length ? input.files[0].name : 'No file selected'; note.hidden = !(input.files && input.files.length); };
      input.addEventListener('change', update);
      update();
    });
  }

  function attachSheetGestures() {
    const sheet = $('#ax2-more-layer');
    if (!sheet || sheet.dataset.ax2GesturesBound) return;
    sheet.dataset.ax2GesturesBound = '1';
    let y0 = 0, t0 = 0, tracking = false;
    const begin = event => {
      if (window.innerWidth >= 600) return;
      const target = event.target.closest('[data-ax2-sheet-handle], [data-ax2-sheet-head]');
      if (!target) return;
      tracking = true; y0 = event.clientY; t0 = performance.now();
    };
    const end = event => {
      if (!tracking) return;
      tracking = false;
      const dy = event.clientY - y0;
      const dt = Math.max(1, performance.now() - t0);
      if (dy > 80 || (dy > 35 && dt < 180)) closeSheet(sheet);
    };
    sheet.addEventListener('pointerdown', begin, { passive: true });
    sheet.addEventListener('pointerup', end, { passive: true });
    sheet.addEventListener('pointercancel', () => { tracking = false; }, { passive: true });
  }


  // ------------------------------------------------------------------
  // SECTION: Public API and init
  // window API and startup.
  // ------------------------------------------------------------------
  const api = {
    __initialized: true,
    openSheet, closeSheet, openMenu, closeMenu,
    syncIndicators, syncMirrors, syncLayerOpenClass, closeTopLayer,
  };
  window.ax2 = api;

  function init() {
    const avatar = $('#ax2-avatar-button');
    const menu = $('#ax2-avatar-menu');
    const scrim = $('#ax2-layer-scrim');
    if (!avatar || !menu || !scrim) return;
    avatar.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); openMenu(menu, avatar); });
    scrim.addEventListener('click', event => { if (event.target === scrim) closeTopLayer(); });
    document.addEventListener('click', event => {
      if (!state.layer) return;
      const target = event.target;
      if (state.layer.matches('.ax2-menu') && !state.layer.contains(target) && !avatar.contains(target)) closeMenu(state.layer);
      else if (state.layer.matches('.ax2-sheet') && !state.layer.contains(target) && state.trigger && !state.trigger.contains(target)) closeSheet(state.layer);
    }, true);
    document.addEventListener('keydown', event => {
      if (!state.layer) return;
      const layer = state.layer;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeTopLayer(); return; }
      const items = focusables(layer);
      const idx = items.indexOf(document.activeElement);
      if (layer.matches('.ax2-menu') && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault();
        const next = event.key === 'ArrowDown' ? (idx + 1 + items.length) % items.length : (idx - 1 + items.length) % items.length;
        items[next]?.focus();
      } else if (event.key === 'Tab' && items.length) {
        if (event.shiftKey && (idx <= 0)) { event.preventDefault(); items[items.length - 1].focus(); }
        else if (!event.shiftKey && (idx === items.length - 1)) { event.preventDefault(); items[0].focus(); }
      }
    }, true);
    document.addEventListener('load', event => {
      if (event.target instanceof HTMLImageElement) { event.target.classList.remove('ax2-image-pending'); event.target.classList.add('is-loaded'); }
    }, true);
    document.addEventListener('focusin', event => {
      if (event.target === $('#gs')) syncHeaderVisibility(true);
    });
    document.addEventListener('focusout', event => {
      if (event.target === $('#gs')) window.setTimeout(() => { if ($('#gs') !== document.activeElement) syncHeaderVisibility(false); }, 0);
    });
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', () => {
      syncIndicators(); syncMirrors(); syncResponsiveLabels(); syncHeaderVisibility(true);
      if (state.layer?.matches('.ax2-menu')) positionMenu(state.layer, state.trigger);
      if (state.layer?.matches('.ax2-sheet')) positionSheetPopover(state.layer, state.trigger);
    }, { passive: true });
    window.addEventListener('popstate', () => { closeAnyLayer(); setTimeout(() => { syncIndicators(); animateView(); }, 0); });
    window.addEventListener('hashchange', () => { syncIndicators(); });
    syncResponsiveLabels();
    let mirrorFrame = 0;
    const scheduleMirrors = () => { if (mirrorFrame) return; mirrorFrame = requestAnimationFrame(() => { mirrorFrame = 0; syncMirrors(); }); };
    const observer = new MutationObserver(scheduleMirrors);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme-mode', 'data-theme'] });
    const bodyObserver = new MutationObserver(scheduleMirrors);
    let layerSyncFrame = 0;
    const layerObserver = new MutationObserver(() => {
      if (layerSyncFrame) return;
      layerSyncFrame = requestAnimationFrame(() => { layerSyncFrame = 0; syncLayerOpenClass(); });
    });
    layerObserver.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class','hidden','aria-hidden','style'] });
    ['#ops-running-count', '#ops-queued-count', '#ops-queue-count', '#tgdot', '#tglabel', '#ax-storage-pct', '#ax-storage-data', '#ax-storage-free'].forEach(selector => {
      const source = $(selector);
      if (source) bodyObserver.observe(source, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class', 'style', 'aria-valuenow'] });
    });
    attachSheetGestures();
    initFileInputNames(document);
    let fileInputFrame = 0;
    const fileInputObserver = new MutationObserver(records => {
      const hasElementAdded = records.some(record => record.type === 'childList' && Array.from(record.addedNodes).some(node => node.nodeType === 1));
      if (!hasElementAdded || fileInputFrame) return;
      fileInputFrame = window.requestAnimationFrame(() => { fileInputFrame = 0; initFileInputNames(document); });
    });
    fileInputObserver.observe(document.body, { childList: true, subtree: true });
    markImages(document);
    markHeadings(document);
    syncIndicators();
    syncMirrors();
    syncLayerOpenClass();

    if (typeof window.showView === 'function' && !window.showView.__ax2Wrapped) {
      const originalShowView = window.showView;
      const wrapped = function (viewName, ...args) {
        const result = originalShowView.call(this, viewName, ...args);
        if (state.layer) closeAnyLayer();
        window.requestAnimationFrame(() => {
          animateView();
          syncIndicators(viewName);
          syncHeaderVisibility(true);
        });
        return result;
      };
      wrapped.__ax2Wrapped = true;
      window.showView = wrapped;
    }
    // Initialize the header and indicators after DOMContentLoaded also catches the initial showView call.
    window.setTimeout(() => { animateView(); syncIndicators(); }, 0);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
