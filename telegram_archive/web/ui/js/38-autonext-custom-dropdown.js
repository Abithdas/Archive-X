/* ============================================================================
 * FILE: js/38-autonext-custom-dropdown.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Custom listbox UI for the autoplay-source select.
   The existing <select> stays authoritative and existing save handlers are preserved. */
(() => {
  'use strict';
  const id = 'v55-autonext-mode';
  function init() {
    const select = document.getElementById(id);
    if (!(select instanceof HTMLSelectElement) || select.dataset.axDropdownReady === '1' || !select.options.length) return;
    const parent = select.parentElement;
    if (!parent) return;

    const wrap = document.createElement('div');
    wrap.className = 'ax-autonext-combobox';
    parent.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add('ax-autonext-native');
    select.dataset.axDropdownReady = '1';
    // Inline important prevents any legacy ID-specific selector from showing
    // the native field above the custom dropdown.
    select.style.setProperty('display', 'none', 'important');

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'ax-autonext-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-label', 'Autoplay source');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    const label = document.createElement('span');
    label.className = 'ax-autonext-trigger-label';
    const caret = document.createElement('span');
    caret.className = 'ax-autonext-chevron';
    caret.setAttribute('aria-hidden', 'true');
    trigger.append(label, caret);

    const menu = document.createElement('div');
    menu.className = 'ax-autonext-menu';
    menu.id = 'ax-autonext-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', 'Autoplay source options');
    menu.hidden = true;
    trigger.setAttribute('aria-controls', menu.id);
    const rows = Array.from(select.options).map((option, index) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'ax-autonext-option';
      row.id = `ax-autonext-option-${index}`;
      row.setAttribute('role', 'option');
      row.tabIndex = -1;
      row.dataset.value = option.value;
      row.textContent = option.textContent || option.value;
      row.addEventListener('pointerenter', () => setActive(index));
      row.addEventListener('click', () => choose(index));
      menu.appendChild(row);
      return row;
    });
    wrap.appendChild(trigger);
    (document.getElementById('ax-ui-scale-root')||document.body).appendChild(menu);
    let openState = false;
    let active = 0;

    function selectedIndex() {
      const n = Array.from(select.options).findIndex(o => o.value === select.value);
      return n < 0 ? 0 : n;
    }
    function sync() {
      const i = selectedIndex();
      label.textContent = select.options[i]?.textContent || 'Choose source';
      trigger.title = label.textContent;
      rows.forEach((row, n) => {
        row.setAttribute('aria-selected', String(n === i));
        row.dataset.active = String(openState && n === active);
      });
    }
    function positionMenu() {
      const r = trigger.getBoundingClientRect();
      // Geometry is measured in visual viewport pixels; descendants of the app
      // zoom root are laid out in pre-zoom CSS pixels.
      const zoom = Math.max(.1, Number(getComputedStyle(document.documentElement).getPropertyValue('--ax-ui-zoom')) || 1);
      const leftEdge=r.left/zoom, rightEdge=r.right/zoom, topEdge=r.top/zoom, bottomEdge=r.bottom/zoom;
      const viewW=window.innerWidth/zoom, viewH=window.innerHeight/zoom;
      const pad = 10, gap = 5;
      const width = Math.min(r.width/zoom, viewW - pad * 2);
      const left = Math.max(pad, Math.min(leftEdge, viewW - width - pad));
      const desired = Math.min(280, rows.length * 40 + 10, viewH - pad * 2);
      const below = viewH - bottomEdge - gap;
      const above = topEdge - gap;
      let top, maxH;
      if (below >= Math.min(desired, 170) || below >= above) {
        maxH = Math.max(100, Math.min(desired, below - pad));
        top = bottomEdge + gap;
      } else {
        maxH = Math.max(100, Math.min(desired, above - pad));
        top = topEdge - gap - maxH;
      }
      top = Math.max(pad, Math.min(top, viewH - pad - maxH));
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
      menu.style.width = `${width}px`;
      menu.style.maxHeight = `${maxH}px`;
      // Final guard: padding/borders can make the painted box taller than max-height, so measure the
      // real box and slide it back inside the visible viewport (it was spilling below the screen on phones).
      const box = menu.getBoundingClientRect();
      const overflow = box.bottom / zoom - (viewH - pad);
      if (overflow > 0) menu.style.top = `${Math.max(pad, top - overflow)}px`;
    }
    function setActive(i) {
      if (!rows.length) return;
      active = Math.max(0, Math.min(rows.length - 1, i));
      rows.forEach((row, n) => { row.dataset.active = String(openState && n === active); });
      const row = rows[active];
      trigger.setAttribute('aria-activedescendant', row.id);
      const y0 = row.offsetTop, y1 = y0 + row.offsetHeight;
      if (y0 < menu.scrollTop) menu.scrollTop = y0;
      else if (y1 > menu.scrollTop + menu.clientHeight) menu.scrollTop = y1 - menu.clientHeight;
    }
    function open() {
      if (openState) return;
      openState = true;
      active = selectedIndex();
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      sync();
      positionMenu();
      setActive(active);
    }
    function close(focus = false) {
      if (!openState) return;
      openState = false;
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.removeAttribute('aria-activedescendant');
      sync();
      if (focus) trigger.focus();
    }
    function choose(i) {
      const option = select.options[i];
      if (!option) return;
      select.value = option.value;
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
      active = i;
      sync();
      close(true);
    }
    trigger.addEventListener('click', () => openState ? close() : open());
    trigger.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); openState ? setActive(active + 1) : open(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); openState ? setActive(active - 1) : open(); }
      else if (e.key === 'Home' && openState) { e.preventDefault(); setActive(0); }
      else if (e.key === 'End' && openState) { e.preventDefault(); setActive(rows.length - 1); }
      else if ((e.key === 'Enter' || e.key === ' ') && openState) { e.preventDefault(); choose(active); }
      else if (e.key === 'Escape' && openState) { e.preventDefault(); close(true); }
    });
    document.addEventListener('pointerdown', e => {
      if (openState && !wrap.contains(e.target) && !menu.contains(e.target)) close();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && openState) close(true); });
    window.addEventListener('resize', () => { if (openState) positionMenu(); });
    window.addEventListener('scroll', () => { if (openState) positionMenu(); }, true);
    menu.addEventListener('pointerdown', e => e.stopPropagation());
    select.addEventListener('change', sync);

    // Existing async settings load assigns select.value directly. Reflect such
    // programmatic assignments on the custom face without altering app handlers.
    try {
      const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
      if (desc?.get && desc?.set) Object.defineProperty(select, 'value', {
        configurable: true, enumerable: desc.enumerable,
        get() { return desc.get.call(this); },
        set(value) { desc.set.call(this, value); queueMicrotask(sync); }
      });
    } catch (_) { /* The custom menu also syncs on open as a fallback. */ }
    const form = select.closest('form');
    if (form) form.addEventListener('reset', () => queueMicrotask(sync));
    sync();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
