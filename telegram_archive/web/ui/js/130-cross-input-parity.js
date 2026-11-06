/* ============================================================================
 * FILE: js/130-cross-input-parity.js
 * AREA: Responsive and input
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X v29.4.290 — cross-input interaction parity. */
(function () {
  'use strict';
  const doc = document;
  const root = doc.documentElement;
  const nativeInteractive = 'a,button,input,select,textarea,summary,option,[contenteditable="true"]';
  const customInteractive = '[role="button"],[role="link"],[role="slider"],[role="tab"],[role="menuitem"]';
  const excluded = '.ax-ux-command,.ax-ux-activity,.ax-ux-filter-drawer,.modal-overlay,.modal-backdrop,.overlay-backdrop,[data-no-keyboard-enhance]';

  function conciseLabel(el) {
    const explicit = el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('data-label');
    if (explicit && explicit.trim()) return explicit.trim().slice(0, 140);
    const text = String(el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    return text ? text.slice(0, 140) : 'Activate item';
  }
  function hasInteractiveChild(el) {
    return !!el.querySelector(`${nativeInteractive},${customInteractive},[tabindex="0"]`);
  }
  function enhanceNode(el) {
    if (!el || el.nodeType !== 1 || !el.matches('[onclick]')) return;
    if (el.closest(excluded) || el.closest('button,a,label,summary,[role="menu"],[role="menuitem"]')) return;
    if (!['DIV', 'SPAN', 'LI', 'ARTICLE', 'SECTION'].includes(el.tagName)) return;
    // A clickable parent with nested actions must not become a nested button.
    if (hasInteractiveChild(el) && !el.hasAttribute('role')) return;
    if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    if (!el.hasAttribute('aria-label')) el.setAttribute('aria-label', conciseLabel(el));
    if (!el.hasAttribute('onkeydown') && !el.hasAttribute('data-ax-keyboard-activate')) {
      el.setAttribute('data-ax-keyboard-activate', 'true');
    }
  }
  function enhanceTree(node) {
    if (!node || node.nodeType !== 1) return;
    enhanceNode(node);
    node.querySelectorAll?.('[onclick]').forEach(enhanceNode);
  }
  function activateKeyboardTarget(ev) {
    if (ev.defaultPrevented || (ev.key !== 'Enter' && ev.key !== ' ')) return;
    const el = ev.target;
    if (!(el instanceof Element) || !el.matches('[data-ax-keyboard-activate]')) return;
    if (el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled')) return;
    ev.preventDefault();
    el.click();
  }
  function isTextEntry(target) {
    return !!(target && target.closest && target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"],[role="searchbox"],[role="combobox"]'));
  }
  function anyBlockingLayerOpen() {
    return !!doc.querySelector('.ax-ux-command.open,.ax-ux-activity.open,.ax-ux-filter-drawer.open,.mdl.open,[aria-modal="true"].open');
  }
  function shortcuts(ev) {
    if (ev.defaultPrevented || ev.altKey || ev.ctrlKey || ev.metaKey || ev.repeat || isTextEntry(ev.target)) return;
    if (anyBlockingLayerOpen()) return;
    const key = String(ev.key || '').toLowerCase();
    // These are the G and / shortcuts already advertised in the command palette.
    if (key === 'g') {
      if (doc.getElementById('do')?.classList.contains('open')) return;
      if (ev.target instanceof Element && ev.target.closest('button,a,[role="button"],[role="tab"],[tabindex="0"]')) return;
      if (typeof window.sv === 'function') { ev.preventDefault(); window.sv('library'); }
    } else if (key === '/') {
      if (doc.getElementById('do')?.classList.contains('open')) return;
      const search = doc.getElementById('gs');
      if (search && !search.disabled && search.getClientRects().length) {
        ev.preventDefault(); search.focus(); search.select?.();
      }
    }
  }
  function bindAccessibleSeekbar() {
    const seek = doc.getElementById('pseek');
    if (!seek || seek.dataset.axKeyboardSeekBound === '1') return;
    seek.dataset.axKeyboardSeekBound = '1';
    seek.setAttribute('role', 'slider');
    seek.setAttribute('tabindex', '0');
    seek.setAttribute('aria-label', 'Seek through video chapters');
    seek.setAttribute('aria-orientation', 'horizontal');
    const getDuration = () => {
      try { return Math.max(0, Number(typeof window.pDur === 'function' ? window.pDur() : doc.getElementById('pv')?.duration) || 0); }
      catch (_) { return 0; }
    };
    const getCurrent = () => {
      try { return Math.max(0, Number(typeof window.pCur === 'function' ? window.pCur() : doc.getElementById('pv')?.currentTime) || 0); }
      catch (_) { return 0; }
    };
    const fmt = seconds => {
      seconds = Math.floor(Math.max(0, Number(seconds) || 0));
      const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
      return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
    };
    const update = () => {
      const duration = getDuration(), current = getCurrent();
      seek.setAttribute('aria-valuemin', '0');
      seek.setAttribute('aria-valuemax', String(Math.floor(duration)));
      seek.setAttribute('aria-valuenow', String(Math.floor(current)));
      seek.setAttribute('aria-valuetext', `${fmt(current)} of ${fmt(duration)}`);
    };
    seek.addEventListener('keydown', ev => {
      let delta = null, current = getCurrent(), duration = getDuration(), target = current;
      if (ev.key === 'ArrowLeft') delta = -5;
      else if (ev.key === 'ArrowRight') delta = 5;
      else if (ev.key === 'PageDown') delta = -30;
      else if (ev.key === 'PageUp') delta = 30;
      else if (ev.key === 'Home') target = 0;
      else if (ev.key === 'End') target = Math.max(0, duration - 0.5);
      else return;
      ev.preventDefault(); ev.stopPropagation();
      if (delta !== null) target = current + delta;
      target = Math.max(0, duration ? Math.min(duration - 0.5, target) : target);
      if (typeof window.pSeek === 'function') window.pSeek(target);
      if (typeof window.pShowUI === 'function') window.pShowUI();
      update();
    });
    ['timeupdate', 'durationchange', 'loadedmetadata', 'seeked', 'emptied'].forEach(type => {
      doc.getElementById('pv')?.addEventListener(type, update, { passive: true });
    });
    update();
  }

  doc.addEventListener('keydown', activateKeyboardTarget);
  doc.addEventListener('keydown', shortcuts);
  enhanceTree(doc.body);
  bindAccessibleSeekbar();
  const observer = new MutationObserver(records => {
    records.forEach(record => record.addedNodes.forEach(enhanceTree));
    bindAccessibleSeekbar();
  });
  observer.observe(doc.body || doc.documentElement, { childList: true, subtree: true });
  window.addEventListener('pageshow', () => { enhanceTree(doc.body); bindAccessibleSeekbar(); });
})();
