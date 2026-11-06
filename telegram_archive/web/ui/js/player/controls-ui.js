/* ============================================================================
 * FILE: js/player/controls-ui.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Player control presentation and A–B repeat UI.
 *
 * The player runtime owns media state and transport. This module owns the
 * control bar's presentation, auto-hide behavior, and repeat-menu interaction.
 */
(function registerPlayerControlsUI(root) {
  'use strict';

  function createControlsUI(options = {}) {
    const getElement = options.getElementById || (id => root.document?.getElementById(id));
    const getState = options.getState || (() => ({}));
    const getPlayer = options.getPlayer || (() => null);
    const getDuration = options.getDuration || (() => 0);
    const getCurrent = options.getCurrent || (() => 0);
    const formatDuration = options.formatDuration || (value => String(value ?? ''));
    const getRepeat = options.getRepeat || (() => null);
    const isPinned = options.isPinned || (() => false);
    const writeText = options.writeTextIfChanged || ((node, value) => {
      const next = String(value ?? '');
      if (node && node.textContent !== next) node.textContent = next;
    });
    const doc = options.document || root.document;
    let menuEventsBound = false;

    function controlsBelowVideo() {
      const top = getElement('dtop'), visual = getElement('pvisual'), bar = getElement('pbar');
      if (!top || !visual || !bar) return false;
      const fullscreen = top.classList.contains('fs') || top.matches(':fullscreen,:-webkit-full-screen');
      if (fullscreen) {
        top.classList.remove('controls-below-video');
        return false;
      }
      const videoRect = visual.getBoundingClientRect(), barRect = bar.getBoundingClientRect();
      const visible = videoRect.width > 0 && videoRect.height > 0 && barRect.width > 0 && barRect.height > 0 && barRect.top >= videoRect.bottom - 1;
      top.classList.toggle('controls-below-video', visible);
      return visible;
    }

    function centerPop() {
      const button = getElement('pbig');
      if (!button || button.classList.contains('ax-center-pop')) return;
      button.classList.add('ax-center-pop');
      root.clearTimeout(button._axCenterPopTimer);
      button._axCenterPopTimer = root.setTimeout(() => button.classList.remove('ax-center-pop'), 520);
    }

    function showUI() {
      const top = getElement('dtop');
      if (!top) return;
      const wasHidden = top.classList.contains('ui-idle') || (!top.classList.contains('uivis') && !top.classList.contains('paused'));
      top.classList.remove('ui-idle');
      top.classList.add('uivis');
      top.classList.remove('hidecur');
      if (wasHidden) centerPop();
      autoHide();
    }

    function autoHide() {
      const state = getState(), top = getElement('dtop'), video = getPlayer();
      root.clearTimeout(state.hideT);
      if (!top) return;
      if (isPinned() || !video || video.paused || state.dragging) {
        top.classList.remove('ui-idle', 'hidecur');
        top.classList.add('uivis');
        return;
      }
      const abMenu = getElement('pab-menu');
      if (abMenu && !abMenu.hidden) return;
      top.classList.remove('ui-idle');
      state.hideT = root.setTimeout(() => {
        const currentVideo = getPlayer();
        if (!isPinned() && currentVideo && !currentVideo.paused && !state.dragging) {
          const active = doc?.activeElement;
          if (active && top.contains(active)) {
            let keyboardFocused = false;
            try { keyboardFocused = active.matches(':focus-visible'); } catch {}
            if (!keyboardFocused) active.blur?.();
          }
          top.classList.remove('paused', 'uivis');
          top.classList.add('ui-idle');
          if (controlsBelowVideo()) top.classList.remove('hidecur');
          else top.classList.add('hidecur');
        }
      }, 2000);
    }

    function renderAB(repeatState) {
      const repeat = getRepeat();
      const state = repeatState || repeat?.snapshot?.() || {};
      const toggle = getElement('pab-toggle'), range = getElement('pab-loop-range'), status = getElement('pab-status');
      const setEnd = getElement('pab-set-b'), clear = getElement('pab-clear');
      if (!toggle || !range || !status || !setEnd || !clear) return;
      const duration = getDuration();
      const currentKey = String(getState().mediaKey || '');
      const valid = !!currentKey && state.mediaKey === currentKey && Number.isFinite(duration) && duration > 0 && state.start !== null;
      const active = valid && state.end !== null;
      const pending = valid && !active;
      toggle.classList.toggle('is-active', !!active);
      toggle.setAttribute('aria-pressed', active ? 'true' : 'false');
      toggle.setAttribute('aria-label', active
        ? `Section repeat active from ${formatDuration(state.start)} to ${formatDuration(state.end)}. Open repeat controls to clear it.`
        : pending
          ? `Section repeat start set at ${formatDuration(state.start)}. Open repeat controls to set the end or clear it.`
          : 'Set up section repeat.');
      setEnd.disabled = !pending;
      clear.disabled = !valid;
      if (!valid) {
        range.hidden = true;
        writeText(status, 'No section loop set.');
        return;
      }
      range.hidden = false;
      const left = Math.max(0, Math.min(100, (state.start / duration) * 100));
      const right = active ? Math.max(left, Math.min(100, (state.end / duration) * 100)) : left;
      range.style.left = `${left}%`;
      range.style.width = active ? `${Math.max(0, right - left)}%` : '2px';
      range.classList.toggle('is-pending', !!pending);
      writeText(status, active
        ? `Repeating ${formatDuration(state.start)}–${formatDuration(state.end)}.`
        : `Start set at ${formatDuration(state.start)}. Set the end after this time.`);
    }

    function bindMenuEvents() {
      if (menuEventsBound || !doc) return;
      menuEventsBound = true;
      doc.addEventListener('pointerdown', event => {
        const controls = getElement('pab-controls'), menu = getElement('pab-menu');
        if (menu && !menu.hidden && controls && !controls.contains(event.target)) closeMenu(false);
      }, true);
      doc.addEventListener('keydown', event => {
        const menu = getElement('pab-menu');
        if (event.key === 'Escape' && menu && !menu.hidden) {
          event.preventDefault();
          event.stopPropagation();
          closeMenu(true);
        }
      }, true);
    }

    function toggleMenu() {
      const menu = getElement('pab-menu'), toggle = getElement('pab-toggle');
      if (!menu || !toggle) return;
      const open = menu.hidden;
      menu.hidden = !open;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      showUI();
      if (open) getElement('pab-set-a')?.focus({ preventScroll: true });
      else toggle.focus({ preventScroll: true });
    }

    function closeMenu(returnFocus = false) {
      const menu = getElement('pab-menu'), toggle = getElement('pab-toggle');
      if (!menu || menu.hidden) return;
      menu.hidden = true;
      toggle?.setAttribute('aria-expanded', 'false');
      if (returnFocus) toggle?.focus({ preventScroll: true });
      autoHide();
    }

    function errorMessage(reason) {
      if (reason === 'start-not-set') return 'Set the loop start before the end.';
      if (reason === 'end-not-after-start') return 'The loop end must be at least a quarter-second after the start.';
      if (reason === 'start-out-of-range') return 'Choose a loop start before the end of the video.';
      return 'Wait for the video timeline to load before setting a loop.';
    }

    function setStart() {
      const result = getRepeat()?.setStart?.() || { ok: false, reason: 'unavailable' };
      if (!result.ok) {
        options.toast?.(errorMessage(result.reason));
        return;
      }
      options.toast?.(`Loop start set at ${formatDuration(result.start)}. Choose an end.`);
    }

    function setEnd() {
      const result = getRepeat()?.setEnd?.() || { ok: false, reason: 'unavailable' };
      if (!result.ok) {
        options.toast?.(errorMessage(result.reason));
        return;
      }
      options.toast?.(`Repeating ${formatDuration(result.start)}–${formatDuration(result.end)}.`);
      closeMenu(true);
    }

    function clearRepeat() {
      getRepeat()?.clear?.();
      options.toast?.('Section repeat cleared.');
      closeMenu(true);
    }

    function resetRepeat(mediaKey = '') {
      getRepeat()?.reset?.(mediaKey);
      closeMenu(false);
    }

    function render() {
      const video = getPlayer();
      if (!video) return;
      const duration = getDuration(), current = getCurrent(), state = getState(), repeat = getRepeat();
      repeat?.syncKey?.();
      renderAB(repeat?.snapshot?.());
      const fraction = duration ? Math.min(1, current / duration) : 0;
      if (!state.dragging) {
        getElement('pplay').style.width = `${fraction * 100}%`;
        getElement('pknob').style.left = `${fraction * 100}%`;
      }
      let bufferedEnd = 0;
      for (let index = 0; index < video.buffered.length; index++) {
        if (video.buffered.start(index) <= video.currentTime + 1) bufferedEnd = Math.max(bufferedEnd, video.buffered.end(index));
      }
      getElement('pbuf').style.width = `${duration ? Math.min(1, (state.off + bufferedEnd) / duration) * 100 : 0}%`;
      getElement('ptime').textContent = `${formatDuration(current) || '0:00'} / ${formatDuration(duration) || '–:––'}`;
      writeText(getElement('ptcur'), formatDuration(current) || '0:00');
      writeText(getElement('ptdur'), formatDuration(duration) || '–:––');
    }

    function dragTo(fraction) {
      getElement('pplay').style.width = `${fraction * 100}%`;
      getElement('pknob').style.left = `${fraction * 100}%`;
      const duration = getDuration();
      if (duration) {
        getElement('ptime').textContent = `${formatDuration(fraction * duration) || '0:00'} / ${formatDuration(duration)}`;
        writeText(getElement('ptcur'), formatDuration(fraction * duration) || '0:00');
      }
    }

    return Object.freeze({
      pControlsBelowVideo: controlsBelowVideo,
      pCenterPop: centerPop,
      pShowUI: showUI,
      pAutoHide: autoHide,
      pUI: render,
      pABRender: renderAB,
      pABBindMenuEvents: bindMenuEvents,
      pABMenuToggle: toggleMenu,
      pABMenuClose: closeMenu,
      pABSetStart: setStart,
      pABSetEnd: setEnd,
      pABClear: clearRepeat,
      pABReset: resetRepeat,
      pDragTo: dragTo
    });
  }

  root.ArchiveXPlayerModules = root.ArchiveXPlayerModules || {};
  root.ArchiveXPlayerModules.createControlsUI = createControlsUI;
})(window);
