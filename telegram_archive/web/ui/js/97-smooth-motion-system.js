/* ============================================================================
 * FILE: js/97-smooth-motion-system.js
 * AREA: Motion and feedback
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* One presentation-only entrance owner. No navigation, data or overlay geometry changes. */
(function () {
  'use strict';
  if (window.AXMotion) return;
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-reduced-motion: reduce)');
  const tabs = '.ax2-settings-panel,.ai-page-tab,.entity-detail-panel,.entity-tab-panel,.ax-manager-step-panel,.ax2-transfer-panel';
  const overlays = '.mdl,dialog,#ux-command,#ux-activity,#ux-filter-drawer,#v54-job-detail,#video-move-dialog,#ax-resume-dialog,.ax2-sheet,.ax2-menu';
  const surfaces = `${tabs},${overlays}`;
  const grids = '#vg,#perf-list,#site-list,#channel-cards';
  const cards = '.vc,.performer-card,.pcard,.ax2-channel-card';
  const entityCardStaggerMs = 48;
  const running = new Map(), shown = new WeakMap();
  let current = null, frame = 0, gridDeadline = 0, enteredGrids = new WeakSet();
  const reduced = () => media.matches || root.dataset.axMotion === 'off';
  const visible = node => !!node && !node.hidden && node.getAttribute('aria-hidden') !== 'true' && node.getClientRects().length > 0 && getComputedStyle(node).visibility !== 'hidden';
  const duration = (token, fallback) => {
    const raw = getComputedStyle(root).getPropertyValue(token).trim();
    const number = parseFloat(raw);
    return Number.isFinite(number) ? number * (raw.endsWith('ms') ? 1 : 1000) : fallback;
  };
  function cancel(node) {
    const animation = running.get(node);
    if (animation) { running.delete(node); animation.cancel(); }
  }
  function stop() { for (const node of Array.from(running.keys())) cancel(node); }
  function enter(node, kind = 'page', delay = 0) {
    if (!visible(node) || reduced() || typeof node.animate !== 'function') return;
    cancel(node);
    const first = { opacity: kind === 'page' ? .35 : .55 };
    const last = { opacity: 1 };
    // Never transform the page/stage or a full-viewport overlay ancestor.
    // Individual properties preserve existing panel centering and hover transforms.
    if (kind === 'heading') { first.translate = '0 5px'; last.translate = '0 0'; }
    if (kind === 'dialog') { first.scale = '.98'; last.scale = '1'; }
    if (kind === 'panel') { first.translate = '12px 0'; last.translate = '0 0'; }
    if (kind === 'card') { first.opacity = 0; first.translate = '0 14px'; last.translate = '0 0'; }
    const durationToken = kind === 'tab' ? '--ax-motion-tab' : kind === 'card' ? '--ax-motion-card' : '--ax-motion-page';
    const durationFallback = kind === 'tab' ? 140 : kind === 'card' ? 260 : 200;
    const animation = node.animate([first, last], {
      duration: duration(durationToken, durationFallback),
      delay, easing: 'cubic-bezier(.2,.8,.2,1)', fill: kind === 'card' ? 'backwards' : 'none',
    });
    running.set(node, animation);
    const cleanup = () => { if (running.get(node) === animation) running.delete(node); };
    animation.finished.then(cleanup, cleanup);
  }
  function enterCards() {
    if (!current || reduced()) return;
    current.querySelectorAll(grids).forEach(grid => {
      const entityGrid = grid.matches('#perf-list,#site-list');
      const videoGrid = grid.matches('#vg');
      // The video renderer marks background data refreshes as quiet; keep those
      // updates from replaying entrance motion or flashing the visible cards.
      if (videoGrid && grid.classList.contains('ax2-quiet-refresh')) return;
      if (!entityGrid && performance.now() > gridDeadline) return;
      if (enteredGrids.has(grid) || (!entityGrid && grid.classList.contains('rx-static'))) return;
      const items = Array.from(grid.children).filter(node => node.matches(cards) && visible(node));
      if (!items.length) return; // Skeletons must not consume the real-content entrance.
      enteredGrids.add(grid);
      if (entityGrid || videoGrid) {
        items.forEach((node, index) => enter(node, 'card', index * entityCardStaggerMs));
        return;
      }
      items.filter(node => {
        const box = node.getBoundingClientRect();
        return box.top < innerHeight && box.bottom > 0;
      }).slice(0, 6).forEach((node, index) => enter(node, 'card', index * 12));
    });
  }
  function replayGrid(gridOrSelector) {
    const grid = typeof gridOrSelector === 'string'
      ? document.querySelector(gridOrSelector)
      : gridOrSelector;
    if (!grid || !grid.matches(grids) || !visible(grid) || reduced()) return;
    // A user-initiated render owns this grid's entrance so the MutationObserver
    // cannot immediately replay the same cards a second time.
    enteredGrids.add(grid);
    Array.from(grid.children)
      .filter(node => node.matches(cards) && visible(node))
      .forEach((node, index) => enter(node, 'card', index * entityCardStaggerMs));
  }
  function sync() {
    frame = 0;
    const next = Array.from(document.querySelectorAll('#view-stage .view.on')).find(visible) || null;
    if (next !== current) {
      stop();
      current = next;
      enteredGrids = new WeakSet();
      gridDeadline = performance.now() + 1200;
      if (next) {
        enter(next);
        const heading = next.querySelector('.library-head,.ax-ops-page-intro,.ax-transfer-page-intro,.ai-page-head,.ax2-page-head');
        if (heading) enter(heading, 'heading');
      }
    }
    document.querySelectorAll(surfaces).forEach(node => {
      const open = visible(node) && (node.matches(tabs)
        ? !!node.closest('.view.on') && (node.classList.contains('on') || node.classList.contains('is-active') || node.classList.contains('active'))
        : (node.classList.contains('open') || (node.matches('dialog') && node.open) || (node.id === 'ax-resume-dialog' && node.getAttribute('aria-hidden') === 'false')));
      const previous = shown.get(node) || false;
      shown.set(node, open);
      if (!open) { cancel(node); return; }
      if (previous) return;
      if (node.matches(tabs)) {
        // Initial active panels already participate in the containing page fade.
        if (!running.has(current)) enter(node, 'tab');
      } else {
        const panel = node.matches('dialog') ? node : node.querySelector('.mdl-box,.channel-edit-box,.ax-ux-panel,.v54-job-panel,.v54-job-detail-panel,.video-move-panel,.ax-resume-card') || node;
        if (panel === node && !node.matches('dialog,.ax2-sheet,.ax2-menu')) return;
        enter(panel, node.matches('#ux-activity,#v54-job-detail,.ax2-sheet') ? 'panel' : 'dialog');
        if (node.matches('.ax2-menu')) {
          node.querySelectorAll('.ax2-menu-item,.ax2-menu-account-actions .btn').forEach((item, index) => {
            if (index < 8) enter(item, 'tab', index * 12);
          });
        }
      }
    });
    enterCards();
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(sync); }
  function preferencesChanged() {
    root.dataset.axMotionReduced = reduced() ? 'true' : 'false';
    if (reduced()) stop();
    schedule();
  }
  function init() {
    if (!root.hasAttribute('data-ax-motion')) root.dataset.axMotion = 'on';
    preferencesChanged();
    const observer = new MutationObserver(records => {
      if (records.some(record => {
        const node = record.target;
        if (record.type === 'attributes') return node.matches?.(`.view,${surfaces},#app`);
        return node.matches?.(grids) || Array.from(record.addedNodes).some(added => added.nodeType === 1 && (added.matches(`.view,${surfaces},${grids}`) || added.querySelector(`.view,${surfaces},${grids}`)));
      })) schedule();
    });
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class','hidden','open','aria-hidden','style'] });
    new MutationObserver(preferencesChanged).observe(root, { attributes: true, attributeFilter: ['data-ax-motion'] });
    if (media.addEventListener) media.addEventListener('change', preferencesChanged);
    else media.addListener(preferencesChanged);
    document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); else schedule(); });
    window.addEventListener('pagehide', stop);
    schedule();
  }
  window.AXMotion = { sync: schedule, stop, replayGrid };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
