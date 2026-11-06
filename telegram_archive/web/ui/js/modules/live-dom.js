/* ============================================================================
 * FILE: js/modules/live-dom.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Reconcile frequently refreshed markup while preserving active DOM state. */
(function createLiveDom(global) {
  function morphKey(node) {
    if (!node || node.nodeType !== 1) return null;
    return node.getAttribute('data-ax-live-key') || node.getAttribute('data-job-key') || null;
  }

  function morphCompatible(current, next) {
    if (!current || !next || current.nodeType !== next.nodeType) return false;
    if (current.nodeType === 1 && current.tagName !== next.tagName) return false;
    const currentKey = morphKey(current);
    const nextKey = morphKey(next);
    if (currentKey || nextKey) return !!currentKey && currentKey === nextKey;
    return true;
  }

  function syncAttributes(current, next) {
    const nextAttributes = new Map(Array.from(next.attributes).map(attribute => [attribute.name, attribute.value]));
    const keepDetailsOpen = current.tagName === 'DETAILS' && current.open && !next.hasAttribute('open');
    Array.from(current.attributes).forEach(attribute => {
      if (!nextAttributes.has(attribute.name) && !(keepDetailsOpen && attribute.name === 'open')) {
        current.removeAttribute(attribute.name);
      }
    });
    nextAttributes.forEach((value, name) => {
      if (!keepDetailsOpen || name !== 'open') {
        if (current.getAttribute(name) !== value) current.setAttribute(name, value);
      }
    });
  }

  function morphChildren(current, next) {
    const oldNodes = Array.from(current.childNodes);
    const unused = new Set(oldNodes);
    const keyed = new Map();
    oldNodes.forEach(node => {
      const key = morphKey(node);
      if (!key) return;
      if (!keyed.has(key)) keyed.set(key, []);
      keyed.get(key).push(node);
    });

    const desired = Array.from(next.childNodes).map((fresh, index) => {
      const key = morphKey(fresh);
      let match = null;
      if (key) {
        match = (keyed.get(key) || []).find(node => unused.has(node) && morphCompatible(node, fresh)) || null;
      } else {
        const at = oldNodes[index];
        if (at && unused.has(at) && !morphKey(at) && morphCompatible(at, fresh)) match = at;
        if (!match) match = oldNodes.find(node => unused.has(node) && !morphKey(node) && morphCompatible(node, fresh)) || null;
      }
      if (!match) return fresh.cloneNode(true);
      unused.delete(match);
      return morphNode(match, fresh);
    });

    desired.forEach((node, index) => {
      const currentNode = current.childNodes[index] || null;
      if (currentNode !== node) current.insertBefore(node, currentNode);
    });
    Array.from(current.childNodes).forEach(node => {
      if (!desired.includes(node)) current.removeChild(node);
    });
  }

  function morphNode(current, next) {
    if (!morphCompatible(current, next)) return next.cloneNode(true);
    if (current.nodeType === 3 || current.nodeType === 8) {
      if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      return current;
    }
    if (current.nodeType !== 1) return current;

    syncAttributes(current, next);
    morphChildren(current, next);

    // Preserve the live input node so focus and text selection survive polling.
    if (current.tagName === 'INPUT') {
      const type = String(current.type || current.getAttribute('type') || '').toLowerCase();
      if (type === 'checkbox' || type === 'radio') current.checked = !!next.checked;
      else if (!global.document || global.document.activeElement !== current) {
        if (current.value !== next.value) current.value = next.value;
      }
    }
    return current;
  }

  function patchLiveMarkup(container, html) {
    if (!container) return;
    // A shimmer skeleton (js/192) stands in for these children until real content arrives.
    if (container.dataset && container.dataset.axSkeleton && global.AXSkeleton) global.AXSkeleton.clear(container);
    const staged = global.document.createElement('div');
    staged.innerHTML = String(html ?? '');
    morphChildren(container, staged);
  }

  function reconcileJobCards(container, staged) {
    const existing = new Map(
      Array.from(container.children)
        .filter(node => node.hasAttribute('data-job-id'))
        .map(node => [String(node.getAttribute('data-job-id')), node])
    );
    const desired = Array.from(staged.children).map(fresh => {
      const id = String(fresh.getAttribute('data-job-id') || '');
      const current = existing.get(id);
      return current ? morphNode(current, fresh) : fresh;
    });
    const keep = new Set(desired);
    desired.forEach((node, index) => {
      const current = container.children[index] || null;
      if (current !== node) container.insertBefore(node, current);
    });
    Array.from(container.children).forEach(node => {
      if (!keep.has(node)) container.removeChild(node);
    });
  }

  global.ArchiveXLiveDom = Object.freeze({ patchLiveMarkup, reconcileJobCards });
})(window);
