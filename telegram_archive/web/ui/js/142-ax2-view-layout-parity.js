/* ============================================================================
 * FILE: js/142-ax2-view-layout-parity.js
 * AREA: Design system and global polish
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* AX2 reference parity: place every top-level library/management view in the shared stage.
   Layout only. The original navigation function and all existing handlers remain authoritative. */
(() => {
  'use strict';
  if (window.__ax2ViewLayoutParityInstalled) return;
  window.__ax2ViewLayoutParityInstalled = true;

  const stagedViews = new Set(['photos', 'library', 'performers', 'sites', 'admin', 'settings', 'ai', 'transfers']);
  const ensureStage = () => {
    if (typeof window.ensureTopLevelViewStage === 'function') {
      window.ensureTopLevelViewStage();
      return;
    }
    const anchor = document.getElementById('mapbar');
    if (!anchor || !anchor.parentNode) return;
    let stage = document.getElementById('view-stage');
    if (!stage) {
      stage = document.createElement('main');
      stage.id = 'view-stage';
      anchor.parentNode.insertBefore(stage, anchor.nextSibling);
    }
    ['vphotos', 'vl', 'vp', 'vsite', 'vtg', 'vs', 'va', 'vai', 'vtransfers'].forEach((id) => {
      const view = document.getElementById(id);
      if (view && view.parentNode !== stage) stage.appendChild(view);
    });
  };

  // Put the initial view in the stage even if the application boot sequence ran before this layer.
  ensureStage();

  const originalShowView = window.showView;
  if (typeof originalShowView === 'function' && !originalShowView.__ax2LayoutParityWrapped) {
    const wrappedShowView = function (view, ...args) {
      if (stagedViews.has(String(view || ''))) ensureStage();
      return originalShowView.call(this, view, ...args);
    };
    wrappedShowView.__ax2LayoutParityWrapped = true;
    window.showView = wrappedShowView;
  }
})();
