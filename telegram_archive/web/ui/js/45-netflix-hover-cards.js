/* ============================================================================
 * FILE: js/45-netflix-hover-cards.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* V29.4.204 — recommendation cards use in-card thumbnail slideshows only.
   Expandable hover popovers are intentionally disabled at the user's request.
   Existing bindCardPreviews() in 09-app.js owns frame loading, layer swapping,
   hover timing, and cleanup for .unx recommendation cards. */
(() => {
  'use strict';
  // Compatibility hook retained for the late rail layer. There is no floating
  // popover to dismiss, so arrow navigation can proceed without interception.
  window._axDismissHoverCardForArrow = function(card) { return false; };
})();
