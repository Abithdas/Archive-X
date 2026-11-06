/* ============================================================================
 * FILE: js/154-ax-account-profile.js
 * AREA: Settings
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X local account profile: display name + photo shown in the header avatar and Settings > Telegram > Account. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const P = window.axAccountProfile = { name: '', customName: '', telegramName: '', hasImage: false, version: '', loaded: false };
  let editState = null, editOpenSeq = 0, focusTimer = 0;
  window.axInvalidatePendingAccountProfileEditOpen = function () { editOpenSeq++; };

  function initials(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
    return (parts.map(x => Array.from(x)[0] || '').join('') || 'A').toUpperCase();
  }
  function photoUrl() {
    return P.hasImage ? '/api/account/profile/image?v=' + encodeURIComponent(P.version || '0') : '/api/telegram/me/photo?v=' + encodeURIComponent(P.version || '0');
  }
  function setCardPhoto(holder, url) {
    if (!holder) return;
    let img = holder.querySelector('img.ax-account-photo');
    if (!url) { if (img) img.remove(); holder.classList.remove('has-photo'); return; }
    if (!img) {
      img = document.createElement('img'); img.className = 'ax-account-photo'; img.alt = '';
      img.onload = () => holder.classList.add('has-photo');
      img.onerror = () => { img.remove(); holder.classList.remove('has-photo'); };
      holder.appendChild(img);
    }
    if (img.getAttribute('src') !== url) img.src = url;
  }
  P.apply = function () {
    const name = P.name;
    document.querySelectorAll('[data-ax-account-name]').forEach(el => { el.textContent = name || 'Telegram account'; });
    document.querySelectorAll('[data-ax-account-initial]').forEach(el => { el.textContent = initials(name); });
    document.querySelectorAll('[data-ax-account-avatar]').forEach(h => setCardPhoto(h, photoUrl()));
    if (typeof window.axRefreshAvatarPhoto === 'function' && P._avatarKey !== (P.hasImage + ':' + P.version)) {
      P._avatarKey = P.hasImage + ':' + P.version; window.axRefreshAvatarPhoto();
    }
  };
  P.refresh = async function () {
    try {
      const r = await window.api('/api/account/profile', { cache: 'no-store' });
      if (!r || r.detail) return;
      P.name = r.name || ''; P.customName = r.custom_name || ''; P.telegramName = r.telegram_name || '';
      P.hasImage = !!r.has_custom_image; P.version = r.image_version || ''; P.loaded = true;
      P.apply();
    } catch (_) {}
  };

  function setStatus(msg, bad) { const el = $('account-edit-status'); if (el) { el.textContent = msg; el.style.color = bad ? 'var(--rx-bad,#e25555)' : ''; } }
  function previewSet(src) {
    const img = $('account-edit-preview'); if (!img) return;
    if (!src) { img.style.display = 'none'; img.removeAttribute('src'); return; }
    img.onerror = () => { img.style.display = 'none'; };
    img.onload = () => { img.style.display = 'block'; };
    img.src = src;
  }
  window.openAccountProfileEdit = async function () {
    const box = $('account-edit-modal'); if (!box) return;
    // Ignore duplicate clicks while the editor is visible or already being prepared.
    if (box.classList.contains('open') || window.__axAccountProfileOpening) return;
    window.__axAccountProfileOpening = true;
    const requestSeq = ++editOpenSeq;
    if (!P.loaded) await P.refresh();
    if (requestSeq !== editOpenSeq) { window.__axAccountProfileOpening = false; return; }
    editState = { imageData: '', reset: false, saving: false };
    const name = $('account-edit-name'); if (name) name.value = P.customName || P.name || '';
    const ini = $('account-edit-initials'); if (ini) ini.textContent = initials(P.name);
    const input = $('account-edit-photo-input'); if (input) input.value = '';
    previewSet(photoUrl());
    setStatus('Change the name, the photo, or both.');
    ['account-edit-save', 'account-edit-choose', 'account-edit-reset', 'account-edit-cancel'].forEach(id => { const b = $(id); if (b) b.disabled = false; });
    box.setAttribute('aria-hidden', 'false');
    box.classList.add('open');
    window.__axAccountProfileOpening = false;
    clearTimeout(focusTimer);
    focusTimer = setTimeout(() => {
      if (requestSeq === editOpenSeq && box.classList.contains('open') && name && name.isConnected) name.focus({ preventScroll: true });
    }, 40);
  };
  window.closeAccountProfileEdit = function () {
    const box = $('account-edit-modal');
    if (!box || !box.classList.contains('open')) {
      // Also invalidate an outstanding async open so it cannot appear after close.
      editOpenSeq++; window.__axAccountProfileOpening = false; return;
    }
    if (editState && editState.saving) return;
    editOpenSeq++; window.__axAccountProfileOpening = false;
    clearTimeout(focusTimer);
    box.classList.remove('open'); box.setAttribute('aria-hidden', 'true');
    editState = null;
  };
  window.accountProfilePhotoChanged = function (input) {
    const file = input && input.files && input.files[0]; if (!editState || !file) return;
    if (file.size > 8 * 1024 * 1024) { input.value = ''; setStatus('Photo must be 8 MiB or smaller.', true); return; }
    if (file.type && !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) { input.value = ''; setStatus('Choose a PNG, JPEG, WebP or GIF image.', true); return; }
    const state = editState, reader = new FileReader();
    reader.onload = () => { if (editState !== state) return; state.imageData = String(reader.result || ''); state.reset = false; previewSet(state.imageData); setStatus('Selected ' + file.name + '. Press Save to apply it.'); };
    reader.onerror = () => setStatus('Could not read that image.', true);
    reader.readAsDataURL(file);
  };
  window.accountProfileResetPhoto = function () {
    if (!editState) return;
    editState.imageData = ''; editState.reset = true;
    const input = $('account-edit-photo-input'); if (input) input.value = '';
    previewSet('/api/telegram/me/photo?refresh=' + Date.now());
    setStatus('Saving will use your Telegram profile photo again.');
  };
  window.saveAccountProfileEdit = async function () {
    const state = editState; if (!state || state.saving) return;
    const name = String(($('account-edit-name') || {}).value || '').trim();
    if (name.length > 64) { setStatus('Display name must be 64 characters or fewer.', true); return; }
    const unchanged = name === (P.customName || '') && !state.imageData && !state.reset;
    if (unchanged) { window.closeAccountProfileEdit(); return; }
    state.saving = true;
    ['account-edit-save', 'account-edit-choose', 'account-edit-reset', 'account-edit-cancel'].forEach(id => { const b = $(id); if (b) b.disabled = true; });
    setStatus('Saving…');
    const body = { name };
    if (state.imageData) body.image_data = state.imageData;
    if (state.reset) body.reset_image = true;
    try {
      const r = await window.api('/api/account/profile', { method: 'POST', body: JSON.stringify(body) });
      if (!r || r.ok === false || r._ok === false || r.detail) throw new Error((r && r.detail) || 'Could not save the profile.');
      state.saving = false;
      P.name = r.name || ''; P.customName = r.custom_name || ''; P.telegramName = r.telegram_name || '';
      P.hasImage = !!r.has_custom_image; P.version = r.image_version || String(Date.now()); P.loaded = true;
      window.closeAccountProfileEdit(); P.apply();
      if (typeof window.toast === 'function') window.toast('Profile updated.');
    } catch (e) {
      state.saving = false;
      ['account-edit-save', 'account-edit-choose', 'account-edit-reset', 'account-edit-cancel'].forEach(id => { const b = $(id); if (b) b.disabled = false; });
      setStatus(String((e && e.message) || e || 'Could not save the profile.'), true);
    }
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('account-edit-modal') && $('account-edit-modal').classList.contains('open')) window.closeAccountProfileEdit(); });
  function boot() { P.refresh(); setInterval(() => { if (!document.hidden) P.refresh(); }, 120000); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else setTimeout(boot, 400);
})();
