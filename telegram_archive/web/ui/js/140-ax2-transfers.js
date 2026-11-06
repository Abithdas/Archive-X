/* ============================================================================
 * FILE: js/140-ax2-transfers.js
 * AREA: Transfers
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X — New Look Step 6: mobile module tabs and transfer source segments only. */
(function () {
  'use strict';
  const storageKey = 'archive-x.transfer-phone-module';
  const modules = ['transfer', 'metadata', 'automation'];
  const stack = () => document.querySelector('#vtransfers .ax-transfer-module-stack');
  const tabs = () => Array.from(document.querySelectorAll('#ax-transfer-phone-tabs [data-transfer-tab]'));
  const source = () => document.getElementById('u-transfer-source');
  const sourceButtons = () => Array.from(document.querySelectorAll('#vtransfers [data-transfer-source-choice]'));

  function savedModule() {
    try {
      const value = window.localStorage.getItem(storageKey);
      return modules.includes(value) ? value : 'transfer';
    } catch (_) {
      return 'transfer';
    }
  }

  function setPhoneModule(name, persist) {
    const active = modules.includes(name) ? name : 'transfer';
    const root = stack();
    if (root) {
      root.classList.remove('ax-transfer-phone-transfer', 'ax-transfer-phone-metadata', 'ax-transfer-phone-automation');
      root.classList.add(`ax-transfer-phone-${active}`);
    }
    tabs().forEach((button) => {
      const selected = button.dataset.transferTab === active;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', selected ? 'true' : 'false');
      button.tabIndex = selected ? 0 : -1;
    });
    if (persist) {
      try { window.localStorage.setItem(storageKey, active); } catch (_) { /* Private browsing/storage policy: session-only selection. */ }
    }
  }

  function syncSourceSegments() {
    const select = source();
    if (!select) return;
    const selectedValue = select.value || 'local';
    sourceButtons().forEach((button) => {
      const selected = button.dataset.transferSourceChoice === selectedValue;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
  }

  function setSource(value) {
    const select = source();
    if (!select || !['local', 'telegram'].includes(value)) return;
    if (select.value !== value) {
      select.value = value;
      // The original onchange hook remains the single source-mode controller.
      select.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (typeof window.unifiedTransferModeChanged === 'function') {
      window.unifiedTransferModeChanged();
    }
    syncSourceSegments();
  }

  function init() {
    setPhoneModule(savedModule(), false);
    tabs().forEach((button) => button.addEventListener('click', () => setPhoneModule(button.dataset.transferTab, true)));
    sourceButtons().forEach((button) => button.addEventListener('click', () => setSource(button.dataset.transferSourceChoice)));
    const select = source();
    if (select) select.addEventListener('change', syncSourceSegments);
    syncSourceSegments();

    // Arrow keys provide a compact, accessible tab interaction on phones.
    const tabList = document.getElementById('ax-transfer-phone-tabs');
    if (tabList) tabList.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const list = tabs();
      const current = list.indexOf(document.activeElement);
      if (current < 0 || !list.length) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length;
      list[next].focus();
      setPhoneModule(list[next].dataset.transferTab, true);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
