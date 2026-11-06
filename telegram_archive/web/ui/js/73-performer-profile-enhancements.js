/* ============================================================================
 * FILE: js/73-performer-profile-enhancements.js
 * AREA: Performers and studios
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* V29.4.204 — Individual performer portrait framing, adaptive profile portrait sizing,
   and related-entity page support. Crop settings are local to this Archive X browser
   profile and keyed per performer ID; they never replace or modify the source image. */
(function () {
  'use strict';

  const STORAGE_KEY = 'archive-x.performer-portrait-framing.v1';
  const DEFAULT_CROP = Object.freeze({ x: 0, y: 0, zoom: 1 });
  let activeState = null;
  let avatarObserver = null;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, Number.isFinite(Number(value)) ? Number(value) : min));
  }

  function cropStoreRead() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      const value = raw ? JSON.parse(raw) : {};
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch (_) {
      return {};
    }
  }

  function cropStoreWrite(store) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  }

  function normalizeCrop(value) {
    const zoom = clamp(Number(value?.zoom ?? DEFAULT_CROP.zoom), 1, 2.5);
    const maxPan = Math.max(0, (zoom - 1) * 47);
    return {
      x: clamp(Number(value?.x ?? DEFAULT_CROP.x), -maxPan, maxPan),
      y: clamp(Number(value?.y ?? DEFAULT_CROP.y), -maxPan, maxPan),
      zoom
    };
  }

  function cropForPerformer(id) {
    const store = cropStoreRead();
    return normalizeCrop(store[String(id)] || DEFAULT_CROP);
  }

  function applyCropToImage(img, crop) {
    if (!img) return;
    const c = normalizeCrop(crop);
    // Keep the established slightly higher default composition while allowing
    // independent translation and zoom for each saved performer.
    img.style.objectPosition = 'center 25%';
    img.style.setProperty('--portrait-pan-x', `${c.x}%`);
    img.style.setProperty('--portrait-pan-y', `${c.y}%`);
    img.style.setProperty('--portrait-zoom', String(c.zoom));
  }

  function resizePortraitZone(zone) {
    if (!zone || !zone.isConnected) return;
    const rect = zone.getBoundingClientRect();
    if (rect.width < 80 || rect.height < 80) return;
    // The portrait grows with the available vertical zone, then caps at a
    // comfortable size bounded by the card width to avoid clipping on mobile.
    const diameter = Math.min(440, rect.width * 0.78, rect.height * 0.90);
    zone.style.setProperty('--performer-avatar-size', `${Math.round(Math.max(150, diameter))}px`);
  }

  window.initPerformerPortraitCard = function (root) {
    const scope = root || document;
    const avatar = scope.querySelector?.('.performer-profile-avatar[data-performer-id]');
    const zone = avatar?.closest('.performer-identity-portrait-zone');
    const img = avatar?.querySelector('img.performer-profile-photo');
    if (!avatar || !zone || !img) return;

    applyCropToImage(img, cropForPerformer(avatar.dataset.performerId));
    if (avatarObserver) avatarObserver.disconnect();
    if (typeof ResizeObserver !== 'undefined') {
      avatarObserver = new ResizeObserver(() => resizePortraitZone(zone));
      avatarObserver.observe(zone);
      const card = avatar.closest('.performer-identity-card');
      if (card) avatarObserver.observe(card);
    }
    requestAnimationFrame(() => resizePortraitZone(zone));
  };

  function updatePreview() {
    if (!activeState) return;
    const frame = document.getElementById('ax-portrait-adjust-preview');
    const img = document.getElementById('ax-portrait-adjust-image');
    const zoom = document.getElementById('ax-portrait-adjust-zoom');
    const zoomValue = document.getElementById('ax-portrait-adjust-zoom-value');
    activeState.crop = normalizeCrop(activeState.crop);
    if (img) applyCropToImage(img, activeState.crop);
    if (zoom) {
      if (Number(zoom.value) !== activeState.crop.zoom) zoom.value = String(activeState.crop.zoom);
      const min = Number(zoom.min) || 1;
      const max = Number(zoom.max) || 2.5;
      const progress = max > min ? ((activeState.crop.zoom - min) / (max - min)) * 100 : 0;
      zoom.style.setProperty('--portrait-zoom-progress', `${clamp(progress, 0, 100)}%`);
    }
    if (zoomValue) zoomValue.textContent = `${Math.round(activeState.crop.zoom * 100)}%`;
    if (frame) frame.classList.toggle('is-dragging', Boolean(activeState.drag));
  }

  function ensureDialog() {
    let overlay = document.getElementById('ax-performer-portrait-adjust');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'ax-performer-portrait-adjust';
    overlay.className = 'ax-portrait-adjust-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = `
      <section class="ax-portrait-adjust-panel" role="dialog" aria-modal="true" aria-labelledby="ax-portrait-adjust-title">
        <header class="ax-portrait-adjust-header">
          <div><h2 id="ax-portrait-adjust-title">Adjust portrait</h2><p id="ax-portrait-adjust-name">Set the framing for this performer.</p></div>
          <button type="button" class="ax-portrait-adjust-close" data-portrait-close aria-label="Close portrait adjustment">×</button>
        </header>
        <div id="ax-portrait-adjust-preview" class="ax-portrait-adjust-preview" aria-label="Portrait crop preview">
          <img id="ax-portrait-adjust-image" alt="Portrait preview" draggable="false">
          <span class="ax-portrait-adjust-guide" aria-hidden="true"></span>
        </div>
        <label class="ax-portrait-adjust-zoom-label" for="ax-portrait-adjust-zoom"><span>Zoom</span><output id="ax-portrait-adjust-zoom-value">100%</output></label>
        <input id="ax-portrait-adjust-zoom" class="ax-portrait-adjust-zoom" type="range" min="1" max="2.5" step="0.01" value="1" aria-label="Portrait zoom">
        <p class="ax-portrait-adjust-help">Drag the photo to set the framing.</p>
        <div id="ax-portrait-adjust-status" class="ax-portrait-adjust-status" role="status" aria-live="polite"></div>
        <footer class="ax-portrait-adjust-actions">
          <button type="button" class="ax-portrait-adjust-reset" data-portrait-reset>Reset</button>
          <span class="ax-portrait-adjust-spacer"></span>
          <button type="button" class="ax-portrait-adjust-cancel" data-portrait-close>Cancel</button>
          <button type="button" class="ax-portrait-adjust-save" data-portrait-save>Save framing</button>
        </footer>
      </section>`;
    const overlayHost = document.getElementById('ax-global-overlay-root') || document.body;
    overlayHost.appendChild(overlay);

    overlay.addEventListener('click', event => {
      if (event.target === overlay || event.target.closest('[data-portrait-close]')) closeDialog();
      if (event.target.closest('[data-portrait-reset]')) {
        if (!activeState) return;
        activeState.crop = { ...DEFAULT_CROP };
        updatePreview();
        const status = document.getElementById('ax-portrait-adjust-status');
        if (status) status.textContent = 'Original framing restored. Save to keep this setting.';
      }
      if (event.target.closest('[data-portrait-save]')) saveCrop();
    });

    const zoom = overlay.querySelector('#ax-portrait-adjust-zoom');
    zoom.addEventListener('input', () => {
      if (!activeState) return;
      activeState.crop.zoom = clamp(Number(zoom.value), 1, 2.5);
      activeState.crop = normalizeCrop(activeState.crop);
      updatePreview();
      const status = document.getElementById('ax-portrait-adjust-status');
      if (status) status.textContent = 'Preview updated. Save to keep this framing.';
    });

    const preview = overlay.querySelector('#ax-portrait-adjust-preview');
    preview.addEventListener('pointerdown', event => {
      if (!activeState || event.button !== 0) return;
      if (activeState.crop.zoom <= 1.001) activeState.crop.zoom = 1.15;
      activeState.drag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        cropX: activeState.crop.x,
        cropY: activeState.crop.y
      };
      try { preview.setPointerCapture(event.pointerId); } catch (_) {}
      updatePreview();
      event.preventDefault();
    });
    preview.addEventListener('pointermove', event => {
      if (!activeState?.drag || activeState.drag.pointerId !== event.pointerId) return;
      const drag = activeState.drag;
      const rect = preview.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const maxPan = Math.max(0, (activeState.crop.zoom - 1) * 47);
      activeState.crop.x = clamp(drag.cropX + (event.clientX - drag.startX) / rect.width * 100, -maxPan, maxPan);
      activeState.crop.y = clamp(drag.cropY + (event.clientY - drag.startY) / rect.height * 100, -maxPan, maxPan);
      updatePreview();
    });
    const endDrag = event => {
      if (!activeState?.drag || activeState.drag.pointerId !== event.pointerId) return;
      activeState.drag = null;
      updatePreview();
      const status = document.getElementById('ax-portrait-adjust-status');
      if (status) status.textContent = 'Preview updated. Save to keep this framing.';
    };
    preview.addEventListener('pointerup', endDrag);
    preview.addEventListener('pointercancel', endDrag);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && overlay.classList.contains('open')) closeDialog();
    });
    return overlay;
  }

  function closeDialog() {
    const overlay = document.getElementById('ax-performer-portrait-adjust');
    if (!overlay) return;
    overlay.classList.remove('open');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('ax-portrait-adjust-open');
    activeState = null;
  }

  function saveCrop() {
    if (!activeState) return;
    const store = cropStoreRead();
    store[String(activeState.id)] = normalizeCrop(activeState.crop);
    try {
      cropStoreWrite(store);
    } catch (_) {
      const status = document.getElementById('ax-portrait-adjust-status');
      if (status) status.textContent = 'Could not save in this browser. Check its local storage settings and try again.';
      return;
    }
    const avatar = document.querySelector(`.performer-profile-avatar[data-performer-id="${String(activeState.id).replace(/[^0-9]/g, '')}"]`);
    const img = avatar?.querySelector('img.performer-profile-photo');
    if (img) applyCropToImage(img, store[String(activeState.id)]);
    closeDialog();
    if (typeof window.toast === 'function') window.toast('Portrait framing saved for this performer.');
  }

  window.openPerformerPortraitAdjust = function (event, performerId) {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    const id = Number(performerId || document.querySelector('.performer-profile-avatar[data-performer-id]')?.dataset.performerId);
    if (!id) return;
    const avatar = document.querySelector(`.performer-profile-avatar[data-performer-id="${id}"]`);
    const photo = avatar?.querySelector('img.performer-profile-photo');
    if (!photo) return;
    const overlay = ensureDialog();
    activeState = {
      id,
      name: String(window._currentPerfEntity?.name || 'Performer'),
      src: String(photo.currentSrc || photo.src || ''),
      crop: cropForPerformer(id),
      drag: null
    };
    const heading = overlay.querySelector('#ax-portrait-adjust-name');
    const img = overlay.querySelector('#ax-portrait-adjust-image');
    const status = overlay.querySelector('#ax-portrait-adjust-status');
    if (heading) heading.textContent = activeState.name;
    if (img) img.src = activeState.src;
    if (status) status.textContent = '';
    overlay.classList.add('open');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('ax-portrait-adjust-open');
    updatePreview();
    overlay.querySelector('[data-portrait-close]')?.focus();
  };
})();
