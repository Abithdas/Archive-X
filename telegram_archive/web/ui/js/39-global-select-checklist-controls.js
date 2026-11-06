/* ============================================================================
 * FILE: js/39-global-select-checklist-controls.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Application-wide custom select UI.
   A hidden native <select> remains authoritative for values and event handlers.
   The custom face is applied to visible, single-select controls across views. */
(() => {
  'use strict';
  if (window.__axGlobalSelectChecklistControls) return;
  window.__axGlobalSelectChecklistControls = true;

  let serial = 0;
  const instances = new Set();
  let scanQueued = false;

  const safeId = value => String(value || 'select').replace(/[^a-zA-Z0-9_-]/g, '-').slice(0, 48);

  function shouldSkip(select) {
    if (!(select instanceof HTMLSelectElement)) return true;
    if (select.dataset.axDropdownReady === '1') return true;
    if (select.multiple || select.size > 1 || select.hidden) return true;
    if (select.getAttribute('aria-hidden') === 'true' || select.style.display === 'none') return true;
    // Keep the theme and performer sort pills native; the video sort uses the
    // shared custom listbox so its popup can match the rest of the application.
    if (select.id === 'v55-theme-mode' || select.id === 'perf-sort-select' || select.matches('[data-native-select="true"], .ax-native-select')) return true;
    return false;
  }

  function accessibleLabel(select) {
    const explicit = select.getAttribute('aria-label');
    if (explicit) return explicit;
    const labels = select.labels ? Array.from(select.labels).map(l => l.textContent.trim()).filter(Boolean) : [];
    if (labels.length) return labels.join(' ');
    const prev = select.previousElementSibling;
    if (prev && /^(LABEL|SPAN|DIV)$/.test(prev.tagName)) {
      const text = prev.textContent.trim();
      if (text && text.length < 100) return text;
    }
    // Common markup: <div><label>Name</label><select>…</select></div>, sometimes with a
    // wrapper inserted between them. Use the field's own label before falling back
    // to the element id (which screen readers announced as e.g. "m auto dst").
    const field = select.parentElement && select.parentElement.closest('div,.fr,.field-wide,label');
    const fieldLabel = field && field.querySelector(':scope > label, :scope > .k');
    if (fieldLabel && !fieldLabel.contains(select)) {
      const text = fieldLabel.textContent.trim();
      if (text && text.length < 100) return text;
    }
    const first = select.options && select.options[0] && select.options[0].textContent.trim();
    if (first) return first;
    return select.id ? select.id.replace(/[-_]+/g, ' ') : 'Choose an option';
  }

  function enhance(select) {
    if (shouldSkip(select)) return;
    const parent = select.parentElement;
    if (!parent) return;

    const rect = select.getBoundingClientRect();
    const parentRect = parent.getBoundingClientRect();
    const computed = getComputedStyle(select);
    const authorWidth = (select.style.width || '').trim().toLowerCase();
    const fullWidth = authorWidth === '100%' ||
      (rect.width > 0 && parentRect.width > 150 && rect.width >= parentRect.width - 10);

    const wrap = document.createElement('div');
    wrap.className = 'ax-autonext-combobox ax-app-select';
    if (fullWidth) wrap.classList.add('ax-select-full-width');

    const minPx = parseFloat(computed.minWidth);
    wrap.style.setProperty('--ax-select-min-width',
      Number.isFinite(minPx) && minPx > 0 && minPx < 600 ? `${minPx}px` : (fullWidth ? '0px' : '9rem'));
    if (!fullWidth && rect.width > 0 && rect.width < 900) {
      wrap.style.setProperty('--ax-select-width', `${Math.max(rect.width, Number.isFinite(minPx) ? minPx : 0)}px`);
    } else {
      wrap.style.setProperty('--ax-select-width', fullWidth ? '100%' : 'max-content');
    }
    const maxPx = parseFloat(computed.maxWidth);
    if (Number.isFinite(maxPx) && maxPx > 0 && maxPx < 1200) {
      wrap.style.setProperty('--ax-select-max-width', `${maxPx}px`);
    }
    if (computed.margin && computed.margin !== '0px') {
      wrap.style.setProperty('--ax-select-margin', computed.margin);
    }

    select.dataset.axDropdownReady = '1';
    select.classList.add('ax-autonext-native');
    // Hide even when older, more-specific ID rules style this select as block.
    select.style.setProperty('display', 'none', 'important');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');

    parent.insertBefore(wrap, select);
    wrap.appendChild(select);

    const instanceId = `${++serial}-${safeId(select.id)}`;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'ax-autonext-trigger';
    trigger.id = `ax-app-select-trigger-${instanceId}`;
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-label', accessibleLabel(select));
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', `ax-app-select-menu-${instanceId}`);

    const label = document.createElement('span');
    label.className = 'ax-autonext-trigger-label';
    const caret = document.createElement('span');
    caret.className = 'ax-autonext-chevron';
    caret.setAttribute('aria-hidden', 'true');
    trigger.append(label, caret);

    const menu = document.createElement('div');
    menu.className = 'ax-autonext-menu';
    if (select.id === 'tb-sort' || select.id === 'ax-photos-sort') menu.classList.add('ax2-library-sort-menu');
    menu.id = `ax-app-select-menu-${instanceId}`;
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', `${trigger.getAttribute('aria-label')} options`);
    menu.hidden = true;

    // Redirect explicit labels to the visible control; the original select ID remains intact for app code.
    Array.from(select.labels || []).forEach(existingLabel => {
      if (existingLabel.htmlFor === select.id) existingLabel.htmlFor = trigger.id;
    });

    let openState = false;
    let active = 0;
    let rows = [];

    function selectedIndex() {
      const found = Array.from(select.options).findIndex(option => option.value === select.value);
      return found < 0 ? Math.max(0, select.selectedIndex) : found;
    }

    function sync() {
      const index = selectedIndex();
      label.textContent = select.options[index]?.textContent?.trim() || 'Choose an option';
      trigger.title = label.textContent;
      trigger.disabled = select.disabled;
      trigger.setAttribute('aria-disabled', String(select.disabled));
      rows.forEach((row, i) => {
        row.setAttribute('aria-selected', String(i === index));
        row.dataset.active = String(openState && i === active);
      });
    }

    function rebuildOptions() {
      menu.replaceChildren();
      rows = Array.from(select.options).map((option, index) => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'ax-autonext-option';
        row.id = `${menu.id}-option-${index}`;
        row.setAttribute('role', 'option');
        row.tabIndex = -1;
        row.dataset.value = option.value;
        row.textContent = option.textContent?.trim() || option.value;
        row.disabled = option.disabled || Boolean(option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled);
        if (row.disabled) row.setAttribute('aria-disabled', 'true');
        row.addEventListener('pointerenter', () => setActive(index));
        row.addEventListener('click', () => choose(index));
        menu.appendChild(row);
        return row;
      });
      if (active >= rows.length) active = Math.max(0, rows.length - 1);
      sync();
    }

    function positionMenu() {
      const r = trigger.getBoundingClientRect();
      const zoom = Math.max(.1, Number(getComputedStyle(document.documentElement).getPropertyValue('--ax-ui-zoom')) || 1);
      const leftEdge=r.left/zoom, topEdge=r.top/zoom, bottomEdge=r.bottom/zoom;
      const viewW=window.innerWidth/zoom, viewH=window.innerHeight/zoom;
      const pad = 10, gap = 5;
      const width = Math.min(Math.max(r.width/zoom, 160), viewW - pad * 2);
      const left = Math.max(pad, Math.min(leftEdge, viewW - width - pad));
      const desired = Math.min(300, Math.max(44, rows.length * 34 + 10), viewH - pad * 2);
      const below = viewH - bottomEdge - gap - pad;
      const above = topEdge - gap - pad;
      let top, maxH;
      if (below >= Math.min(desired, 160) || below >= above) {
        maxH = Math.max(44, Math.min(desired, below));
        top = bottomEdge + gap;
      } else {
        maxH = Math.max(44, Math.min(desired, above));
        top = topEdge - gap - maxH;
      }
      top = Math.max(pad, Math.min(top, viewH - pad - maxH));
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
      menu.style.width = `${width}px`;
      menu.style.maxHeight = `${maxH}px`;
      // Post-layout guard: slide back inside the viewport if padding/borders made the box taller than maxH.
      const box = menu.getBoundingClientRect();
      const overflow = box.bottom / zoom - (viewH - pad);
      if (overflow > 0) menu.style.top = `${Math.max(pad, top - overflow)}px`;
    }

    function setActive(index) {
      if (!rows.length) return;
      let next = Math.max(0, Math.min(rows.length - 1, index));
      const dir = index >= active ? 1 : -1;
      let guard = rows.length;
      while (rows[next]?.disabled && guard-- > 0) {
        next += dir;
        if (next < 0) next = rows.length - 1;
        if (next >= rows.length) next = 0;
      }
      active = next;
      rows.forEach((row, i) => { row.dataset.active = String(openState && i === active); });
      const row = rows[active];
      if (row) {
        trigger.setAttribute('aria-activedescendant', row.id);
        const y0 = row.offsetTop, y1 = y0 + row.offsetHeight;
        if (y0 < menu.scrollTop) menu.scrollTop = y0;
        else if (y1 > menu.scrollTop + menu.clientHeight) menu.scrollTop = y1 - menu.clientHeight;
      }
    }

    function open() {
      if (openState || select.disabled) return;
      if (!rows.length) rebuildOptions();
      openState = true;
      active = selectedIndex();
      if (rows[active]?.disabled) setActive(active + 1);
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      sync();
      positionMenu();
      setActive(active);
    }

    function close(restoreFocus = false) {
      if (!openState) return;
      openState = false;
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.removeAttribute('aria-activedescendant');
      sync();
      if (restoreFocus) trigger.focus();
    }

    function choose(index) {
      const option = select.options[index];
      if (!option || option.disabled || select.disabled) return;
      select.value = option.value;
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
      active = index;
      sync();
      close(true);
    }

    trigger.addEventListener('click', () => openState ? close() : open());
    // tb-sort is visually a single label pill with its icon beside the custom
    // trigger. Prevent label activation from forwarding icon/empty-space clicks
    // to the hidden native select, and make the whole pill open the listbox.
    if ((select.id === 'tb-sort' || select.id === 'ax-photos-sort') && parent instanceof HTMLLabelElement) {
      parent.addEventListener('click', event => {
        if (event.target instanceof Element && event.target.closest('.ax-autonext-trigger')) return;
        event.preventDefault();
        openState ? close() : open();
      });
    }
    trigger.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); if (!openState) open(); else setActive(active + 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); if (!openState) open(); else setActive(active - 1); }
      else if (event.key === 'Home' && openState) { event.preventDefault(); setActive(0); }
      else if (event.key === 'End' && openState) { event.preventDefault(); setActive(rows.length - 1); }
      else if ((event.key === 'Enter' || event.key === ' ') && openState) { event.preventDefault(); choose(active); }
      else if (event.key === 'Escape' && openState) { event.preventDefault(); close(true); }
      else if (event.key.length === 1 && /^[a-z0-9 ]$/i.test(event.key) && rows.length) {
        const start = selectedIndex();
        const needle = event.key.toLowerCase();
        for (let step = 1; step <= rows.length; step++) {
          const index = (start + step) % rows.length;
          if (!rows[index].disabled && rows[index].textContent.trim().toLowerCase().startsWith(needle)) {
            if (!openState) open();
            setActive(index);
            break;
          }
        }
      }
    });

    document.addEventListener('pointerdown', event => {
      if (openState && !wrap.contains(event.target) && !menu.contains(event.target)) close();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && openState) close(true);
    });
    window.addEventListener('resize', () => { if (openState) positionMenu(); });
    window.addEventListener('scroll', () => { if (openState) positionMenu(); }, true);
    menu.addEventListener('pointerdown', event => event.stopPropagation());
    select.addEventListener('change', sync);
    select.addEventListener('input', sync);

    // Direct select.value assignments in the app update the custom face immediately.
    ['value', 'selectedIndex'].forEach(property => {
      try {
        const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, property);
        if (descriptor?.get && descriptor?.set) Object.defineProperty(select, property, {
          configurable: true,
          enumerable: descriptor.enumerable,
          get() { return descriptor.get.call(this); },
          set(value) { descriptor.set.call(this, value); queueMicrotask(sync); }
        });
      } catch (_) { /* Opening the control also syncs as a fallback. */ }
    });

    if (select.form) select.form.addEventListener('reset', () => queueMicrotask(sync));
    wrap.appendChild(trigger);
    (document.getElementById('ax-ui-scale-root')||document.body).appendChild(menu);
    rebuildOptions();

    const optionObserver = new MutationObserver(() => {
      rebuildOptions();
      if (openState) positionMenu();
    });
    optionObserver.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'label', 'value', 'selected'] });
    instances.add({ select, wrap, menu, optionObserver });
  }

  function markChecklistRows() {
    document.querySelectorAll('label').forEach(label => {
      if (label.querySelector(':scope > input[type="checkbox"], :scope > input[type="radio"]') && !label.classList.contains('ax-check-row')) {
        label.classList.add('ax-check-row');
      }
    });
  }

  function scan() {
    scanQueued = false;
    for (const instance of Array.from(instances)) {
      if (!instance.select.isConnected) {
        instance.optionObserver.disconnect();
        instance.menu.remove();
        instances.delete(instance);
      }
    }
    document.querySelectorAll('select').forEach(enhance);
    markChecklistRows();
  }

  function scheduleScan() {
    if (scanQueued) return;
    scanQueued = true;
    requestAnimationFrame(scan);
  }

  function init() {
    scan();
    const observer = new MutationObserver(records => {
      const hasAddedElement = records.some(record => record.type === 'childList' && Array.from(record.addedNodes).some(node => node.nodeType === 1));
      if (hasAddedElement) scheduleScan();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
