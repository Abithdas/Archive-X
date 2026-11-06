/* ============================================================================
 * FILE: js/145-ax-final-parity.js
 * AREA: Design system and global polish
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X — final parity runtime (v29.4.312). UI-only, no observers, no polling. */
(function () {
  "use strict";
  // The library shows its empty state ("Select channels to see their videos.") even before any
  // channel has ever been picked, instead of an empty area under the "Library" heading.
  function ensureLibraryEmptyState() {
    try {
      const grid = document.getElementById("vg");
      if (!grid || grid.firstElementChild) return;
      if (typeof _sel !== "undefined" && _sel && _sel.size) return;
      if (typeof renderGrid === "function") renderGrid();
    } catch (e) { /* the app's own render path stays authoritative */ }
  }
  const originalSv = window.sv;
  if (typeof originalSv === "function") {
    window.sv = function (...args) {
      const result = originalSv.apply(this, args);
      if (args[0] === "library") requestAnimationFrame(ensureLibraryEmptyState);
      return result;
    };
  }
  window.addEventListener("load", () => setTimeout(ensureLibraryEmptyState, 400), { once: true });
})();
