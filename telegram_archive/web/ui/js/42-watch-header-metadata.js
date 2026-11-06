/* ============================================================================
 * FILE: js/42-watch-header-metadata.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Move existing title, metadata chips and source link into the player header.
   Reuses the original DOM nodes/IDs so the existing fillDetail() updates keep working. */
(function () {
  'use strict';
  function setupWatchHeader() {
    const bar = document.getElementById('dtopbar');
    if (!bar || bar.dataset.axWatchHeaderReady === '1') return;
    const title = document.getElementById('dtitle');
    const filename = document.getElementById('dfname');
    const meta = document.getElementById('dmeta');
    const source = document.getElementById('dsource');
    // Capture the legacy Source card before moving #dsource into the top bar.
    // Once the original node is moved, source.closest('.video-source-block')
    // returns null, which previously left an empty Source card in the details column.
    const legacySourceBlocks = Array.from(document.querySelectorAll('#video-tab-details .video-source-block'));
    const legacyTitle = document.getElementById('dbar-t');
    if (!title || !filename || !meta || !source) return;

    let info = bar.querySelector('.ax-watch-header-info');
    if (!info) {
      info = document.createElement('div');
      info.className = 'ax-watch-header-info';
    }
    let titleBlock = info.querySelector('.ax-watch-title-block');
    if (!titleBlock) {
      titleBlock = document.createElement('div');
      titleBlock.className = 'ax-watch-title-block';
      titleBlock.append(title, filename);
      info.appendChild(titleBlock);
    }
    let metaBlock = info.querySelector('.ax-watch-meta-block');
    if (!metaBlock) {
      metaBlock = document.createElement('div');
      metaBlock.className = 'ax-watch-meta-block';
      metaBlock.append(meta, source);
      info.appendChild(metaBlock);
    }

    if (legacyTitle) legacyTitle.classList.add('ax-watch-legacy-title');
    const back = bar.querySelector('.universal-back-btn');
    if (back && info.parentElement !== bar) back.insertAdjacentElement('afterend', info);
    else if (!info.parentElement) bar.prepend(info);

    // Source now lives in the top metadata bar, so remove its empty legacy panel.
    // Remove all stale copies defensively; the source node itself is already in the bar.
    legacySourceBlocks.forEach(block => { if (!block.contains(source)) block.remove(); });
    document.querySelectorAll('#video-tab-details .video-source-block').forEach(block => block.remove());

    // Keep only the single Telegram source action in the header. Other video
    // actions remain in the Actions section (except the duplicate Telegram link).
    const actions = bar.querySelector('.video-page-head-actions');
    if (actions) {
      actions.querySelectorAll('button').forEach((button) => {
        if (button.getAttribute('onclick') !== 'openVideoTelegram()') button.remove();
      });
    }
    bar.dataset.axWatchHeaderReady = '1';
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setupWatchHeader, { once: true });
  else setupWatchHeader();
  // openD() fills the same moved nodes; repeated setup is intentionally idempotent.
  window.addEventListener('pageshow', setupWatchHeader);
})();
