/* ============================================================================
 * FILE: js/35-custom-theme-system.js
 * AREA: Theming
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Constants and editor state
 *   - Palette math and storage
 *   - Applying palettes
 *   - Editor and theme cards
 *   - Integration and init
 * ============================================================================ */
/* Global custom themes. Changes semantic palette tokens only; playback/runtime code is not replaced. */
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // SECTION: Constants and editor state
  // Storage keys, palette fields and editor state.
  // ------------------------------------------------------------------
  const STORE_KEY = 'ax_custom_themes_v1';
  const ACTIVE_KEY = 'ax_active_custom_theme';
  const fields = [
    ['bg', 'Background', '#2e3440'],
    ['surface', 'Surface', '#3b4252'],
    ['primary', 'Primary', '#88c0d0'],
    ['secondary', 'Secondary', '#81a1c1'],
    ['text', 'Text', '#eceff4'],
    ['subtext', 'Subtext', '#c3ccda'],
    ['border', 'Border', 'rgba(236, 239, 244, 0.10)'],
    ['hover', 'Hover', 'rgba(255, 255, 255, 0.08)']
  ];
  const defaults = {
    id: '', name: 'My Theme', isDark: true,
    palette: { bg:'#2e3440', surface:'#3b4252', primary:'#88c0d0', secondary:'#81a1c1', text:'#eceff4', subtext:'#c3ccda', border:'rgba(236, 239, 244, 0.10)', hover:'rgba(255, 255, 255, 0.08)' }
  };
  const tokenNames = [
    '--ax-preset-bg','--ax-preset-surface','--ax-preset-surface-alt','--ax-preset-elevated','--ax-preset-selected',
    '--ax-preset-accent','--ax-preset-accent-hover','--ax-preset-secondary','--ax-preset-text','--ax-preset-subtext',
    '--ax-preset-muted','--ax-preset-on-accent','--ax-preset-border','--ax-preset-border-strong',
    '--ax-overlay-light','--ax-overlay-dark','--ax-scrim'
  ];
  let editing = null;
  let editingIsNew = false;
  let beforeEdit = null;
  let applyingDraft = false;
  let editingMode = 'dark';

  function root() { return document.documentElement; }

  // ------------------------------------------------------------------
  // SECTION: Palette math and storage
  // Colour validation, variants and stored themes.
  // ------------------------------------------------------------------
  function validColor(value) {
    const v = String(value || '').trim();
    if (!v || v.length > 96 || /[;{}<>\\]/.test(v)) return false;
    try { return !!(window.CSS && CSS.supports && CSS.supports('color', v)); } catch (_) { return false; }
  }
  function rgbaOrHex(value) {
    try {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      ctx.fillStyle = String(value || '');
      const normalized = ctx.fillStyle;
      let match = normalized.match(/^#([0-9a-f]{6})$/i);
      if (match) return [parseInt(match[1].slice(0,2),16),parseInt(match[1].slice(2,4),16),parseInt(match[1].slice(4,6),16)];
      match = normalized.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
      return match ? [Number(match[1]),Number(match[2]),Number(match[3])] : null;
    } catch (_) { return null; }
  }
  function blendColor(a, b, amount) {
    const ca = rgbaOrHex(a), cb = rgbaOrHex(b);
    if (!ca || !cb) return b;
    return '#' + ca.map((v,i)=>Math.max(0,Math.min(255,Math.round(v*(1-amount)+cb[i]*amount))).toString(16).padStart(2,'0')).join('');
  }
  function deriveVariantPalette(source, targetMode) {
    if (targetMode === 'light') {
      return {
        bg: blendColor(source.bg, '#f4f6f5', .91), surface: blendColor(source.surface, '#ffffff', .94),
        primary: blendColor(source.primary, '#173d2e', .42), secondary: blendColor(source.secondary, '#425c50', .48),
        text: '#17221c', subtext: '#536159', border: 'rgba(23,34,28,.17)', hover: 'rgba(23,34,28,.06)'
      };
    }
    return {
      bg: blendColor(source.bg, '#101312', .72), surface: blendColor(source.surface, '#1c211e', .68),
      primary: blendColor(source.primary, '#e1fff0', .10), secondary: blendColor(source.secondary, '#a4b5ac', .24),
      text: '#f4f7f5', subtext: '#b7c3bc', border: 'rgba(244,247,245,.16)', hover: 'rgba(255,255,255,.07)'
    };
  }
  function normalizePalette(raw, fallback) {
    const palette = {};
    for (const [key, , defaultValue] of fields) {
      const value = raw && typeof raw[key] === 'string' ? raw[key].trim() : (fallback && fallback[key]) || defaultValue;
      if (!validColor(value)) return null;
      palette[key] = value;
    }
    return palette;
  }
  function normalizeTheme(item) {
    if (!item || typeof item !== 'object') return null;
    const id = String(item.id || '');
    const name = String(item.name || 'My Theme').trim().slice(0, 40) || 'My Theme';
    if (!/^ax-custom-[a-z0-9-]{4,70}$/.test(id)) return null;
    const isDark = item.isDark !== false;
    const legacyPalette = normalizePalette(item.palette, defaults.palette);
    if (!legacyPalette) return null;
    let palettes = {};
    if (item.palettes && typeof item.palettes === 'object') {
      if (item.palettes.dark && typeof item.palettes.dark === 'object') palettes.dark = normalizePalette(item.palettes.dark, isDark ? legacyPalette : null);
      if (item.palettes.light && typeof item.palettes.light === 'object') palettes.light = normalizePalette(item.palettes.light, !isDark ? legacyPalette : null);
    }
    const baseMode = isDark ? 'dark' : 'light';
    if (!palettes[baseMode]) palettes[baseMode] = legacyPalette;
    const otherMode = baseMode === 'dark' ? 'light' : 'dark';
    if (!palettes[otherMode]) palettes[otherMode] = deriveVariantPalette(palettes[baseMode], otherMode);
    if (!palettes.dark || !palettes.light) return null;
    return { id, name, isDark, palette: { ...(palettes[baseMode] || legacyPalette) }, palettes };
  }
  function readThemes() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORE_KEY) || '[]');
      if (!Array.isArray(parsed)) return [];
      const normalized = parsed.map(normalizeTheme).filter(Boolean).slice(0, 100);
      // One-time migration: older saved themes contained only one palette. Persist
      // the derived paired light/dark variants so the mode switch survives reloads.
      if (parsed.some(item => !item || !item.palettes || !item.palettes.dark || !item.palettes.light)) {
        try { localStorage.setItem(STORE_KEY, JSON.stringify(normalized)); } catch (_) {}
      }
      return normalized;
    } catch (_) { return []; }
  }
  function writeThemes(themes) {
    localStorage.setItem(STORE_KEY, JSON.stringify(themes));
  }
  function activeId() { try { return localStorage.getItem(ACTIVE_KEY) || ''; } catch (_) { return ''; } }
  function findTheme(id) { return readThemes().find(theme => theme.id === id) || null; }

  // ------------------------------------------------------------------
  // SECTION: Applying palettes
  // Tokens and preset selection.
  // ------------------------------------------------------------------
  function safeMix(a, percent, b) { return `color-mix(in srgb, ${a} ${percent}%, ${b})`; }
  function readableOn(color) {
    try {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return '#ffffff';
      ctx.fillStyle = color;
      const normalized = ctx.fillStyle;
      let rgb = normalized.match(/^#([0-9a-f]{6})$/i);
      let components;
      if (rgb) components = [parseInt(rgb[1].slice(0,2),16),parseInt(rgb[1].slice(2,4),16),parseInt(rgb[1].slice(4,6),16)];
      else {
        rgb = normalized.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
        if (!rgb) return '#ffffff';
        components = [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
      }
      const linear = components.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); });
      const luminance = .2126*linear[0] + .7152*linear[1] + .0722*linear[2];
      return luminance > .43 ? '#101510' : '#ffffff';
    } catch (_) { return '#ffffff'; }
  }
  function clearTokens() {
    const r = root();
    tokenNames.forEach(name => r.style.removeProperty(name));
    r.removeAttribute('data-ax-custom-theme');
  }
  function applyPalette(theme, persistActive, modeOverride) {
    if (!theme || !theme.palette) return false;
    const r = root();
    const mode = modeOverride || (theme === editing ? editingMode : (r.dataset.themeMode === 'light' ? 'light' : r.dataset.themeMode === 'dark' ? 'dark' : (theme.isDark ? 'dark' : 'light')));
    const source = { ...theme, id: theme.id || 'ax-custom-preview' };
    if (theme === editing) source.palettes = { ...(theme.palettes || {}), [mode]: { ...theme.palette } };
    const clean = normalizeTheme(source);
    if (!clean) return false;
    const p = clean.palettes[mode] || clean.palette;
    if (theme === editing) { editing.palettes = clean.palettes; editing.palette = { ...p }; editing.isDark = mode === 'dark'; }
    const values = {
      '--ax-preset-bg': p.bg,
      '--ax-preset-surface': p.surface,
      '--ax-preset-surface-alt': safeMix(p.surface, 88, p.text),
      '--ax-preset-elevated': safeMix(p.surface, 76, p.text),
      '--ax-preset-selected': safeMix(p.surface, 70, p.primary),
      '--ax-preset-accent': p.primary,
      '--ax-preset-accent-hover': safeMix(p.primary, 86, p.text),
      '--ax-preset-secondary': p.secondary,
      '--ax-preset-text': p.text,
      '--ax-preset-subtext': p.subtext,
      '--ax-preset-muted': safeMix(p.subtext, 74, p.bg),
      '--ax-preset-on-accent': readableOn(p.primary),
      '--ax-preset-border': p.border,
      '--ax-preset-border-strong': safeMix(p.border, 62, p.text),
      '--ax-overlay-light': p.hover,
      '--ax-overlay-dark': mode === 'dark' ? 'rgba(0,0,0,.48)' : 'rgba(0,0,0,.14)',
      '--ax-scrim': mode === 'dark' ? 'rgba(0,0,0,.64)' : 'rgba(0,0,0,.28)'
    };
    Object.entries(values).forEach(([key, value]) => r.style.setProperty(key, value));
    r.dataset.theme = mode;
    r.dataset.themeMode = mode;
    r.dataset.themePreset = 'custom';
    r.dataset.axCustomTheme = clean.id;
    r.style.colorScheme = mode;
    if (persistActive && clean.id !== 'ax-custom-preview') {
      localStorage.setItem(ACTIVE_KEY, clean.id);
      localStorage.setItem('ax_theme_mode', localStorage.getItem('ax_theme_mode') === 'system' ? 'system' : mode);
      localStorage.setItem('ax_theme_preset', 'custom');
    }
    syncPresetSelection();
    renderThemeCards();
    return true;
  }
  function syncPresetSelection() {
    document.querySelectorAll('[data-theme-option]').forEach(button => {
      const active = button.dataset.themeOption === root().dataset.themePreset && root().dataset.themePreset !== 'custom';
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    const status = document.getElementById('ax-appearance-status');
    if (status && root().dataset.themePreset === 'custom') {
      const current = findTheme(activeId());
      status.textContent = current ? `Custom theme “${current.name}” is applied across Archive X.` : 'Custom palette preview is active.';
    }
  }
  function getCurrentPalette() {
    const style = getComputedStyle(root());
    const values = {
      bg: '--ax-preset-bg', surface: '--ax-preset-surface', primary: '--ax-preset-accent', secondary: '--ax-preset-secondary',
      text: '--ax-preset-text', subtext: '--ax-preset-subtext', border: '--ax-preset-border', hover: '--ax-overlay-light'
    };
    const palette = {};
    Object.entries(values).forEach(([key, variable]) => { palette[key] = style.getPropertyValue(variable).trim() || defaults.palette[key]; });
    // If CSS has not resolved the tokens yet, use the Nord palette.
    for (const [key] of fields) if (!validColor(palette[key])) palette[key] = defaults.palette[key];
    return palette;
  }
  function getBeforeEdit() {
    return { custom: activeId(), preset: root().dataset.themePreset || 'nord', mode: root().dataset.themeMode || 'dark' };
  }

  // ------------------------------------------------------------------
  // SECTION: Editor and theme cards
  // Editor UI, cards, selection and delete.
  // ------------------------------------------------------------------
  function setEditorStatus(message, isError) {
    const node = document.getElementById('ax-custom-theme-editor-status');
    if (!node) return;
    node.textContent = message || '';
    node.classList.toggle('error', !!isError);
  }
  function updateDraftFromInput(input) {
    if (!editing) return;
    if (input.id === 'ax-custom-theme-name') {
      editing.name = input.value.trim().slice(0,40) || 'My Theme';
      const title = document.getElementById('ax-custom-theme-editor-title');
      if (title) title.textContent = editingIsNew ? 'Create Theme' : 'Edit Theme';
      renderThemeCards();
      return;
    }
    if (input.dataset.customBase) {
      editing.palettes[editingMode] = { ...editing.palette };
      editingMode = input.dataset.customBase === 'dark' ? 'dark' : 'light';
      editing.isDark = editingMode === 'dark';
      editing.palette = { ...(editing.palettes[editingMode] || deriveVariantPalette(editing.palette, editingMode)) };
      editing.palettes[editingMode] = { ...editing.palette };
      document.querySelectorAll('[data-custom-base]').forEach(button => {
        const pressed = button.dataset.customBase === editingMode;
        button.classList.toggle('active', pressed);
        button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
      });
      if (applyPalette(editing, false, editingMode)) setEditorStatus('Live preview active. Save to keep this theme.');
      return;
    }
    const key = input.dataset.customColor;
    if (!key) return;
    const value = input.value.trim();
    const textInput = document.querySelector(`[data-custom-color-text="${key}"]`);
    if (input.type === 'color') {
      if (textInput) textInput.value = value;
      editing.palette[key] = value;
    } else {
      editing.palette[key] = value;
      const picker = document.querySelector(`[data-custom-color-picker="${key}"]`);
      if (picker && /^#(?:[0-9a-f]{6})$/i.test(value)) picker.value = value;
    }
    if (!validColor(value)) { input.setAttribute('aria-invalid','true'); setEditorStatus(`Enter a valid CSS color for ${fields.find(row=>row[0]===key)?.[1]||key}.`, true); return; }
    input.removeAttribute('aria-invalid');
    editing.palettes = editing.palettes || {}; editing.palettes[editingMode] = { ...editing.palette };
    if (applyPalette(editing, false, editingMode)) setEditorStatus('Live preview active. Save to keep this theme.');
  }
  function pickerColor(value) {
    if (/^#[0-9a-f]{6}$/i.test(value)) return value;
    if (/^#[0-9a-f]{3}$/i.test(value)) return '#' + value.slice(1).split('').map(c=>c+c).join('');
    const m = String(value).match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    if (m) return '#' + [m[1],m[2],m[3]].map(v=>Number(v).toString(16).padStart(2,'0')).join('');
    return '#808080';
  }
  function renderEditor() {
    const panel = document.getElementById('ax-custom-theme-editor');
    const fieldsHost = document.getElementById('ax-custom-theme-fields');
    const name = document.getElementById('ax-custom-theme-name');
    const title = document.getElementById('ax-custom-theme-editor-title');
    if (!panel || !fieldsHost) return;
    if (!editing) { panel.hidden = true; return; }
    panel.hidden = false;
    if (name) name.value = editing.name;
    if (title) title.textContent = editingIsNew ? 'Create Theme' : 'Edit Theme';
    document.querySelectorAll('[data-custom-base]').forEach(button => {
      const pressed = button.dataset.customBase === editingMode;
      button.classList.toggle('active', pressed);
      button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    });
    const deleteButton = panel.querySelector('.ax-custom-theme-delete');
    if (deleteButton) deleteButton.style.visibility = editingIsNew ? 'hidden' : 'visible';
    fieldsHost.innerHTML = fields.map(([key, label]) => {
      const value = editing.palette[key];
      return `<label class="ax-custom-theme-field"><span>${label}</span><span class="ax-custom-theme-field-control"><input aria-label="${label} color picker" type="color" data-custom-color="${key}" data-custom-color-picker="${key}" value="${pickerColor(value)}"><input aria-label="${label} color value" type="text" data-custom-color="${key}" data-custom-color-text="${key}" value="${escapeAttr(value)}" spellcheck="false" autocomplete="off"></span></label>`;
    }).join('');
    fieldsHost.querySelectorAll('[data-custom-color]').forEach(input => input.addEventListener(input.type === 'color' ? 'input' : 'change', () => updateDraftFromInput(input)));
    setEditorStatus('Changes preview live. Save and Apply to keep them.');
  }
  function escapeAttr(value) {
    return String(value).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  function renderThemeCards() {
    const host = document.getElementById('ax-custom-theme-list');
    const count = document.getElementById('ax-custom-theme-count');
    if (!host) return;
    const themes = readThemes();
    if (editingIsNew && editing && !themes.some(theme => theme.id === editing.id)) themes.push(editing);
    if (count) count.textContent = `${readThemes().length} saved`;
    host.innerHTML = themes.length ? themes.map(theme => {
      const active = theme.id === activeId() || (editing && editing.id === theme.id && root().dataset.themePreset === 'custom');
      const dark = theme.palettes.dark, light = theme.palettes.light;
      return `<div class="ax-custom-theme-card"><button type="button" class="ax-theme-option ax-custom-theme-option${active?' active':''}" data-custom-theme-id="${escapeAttr(theme.id)}" aria-pressed="${active?'true':'false'}" title="${escapeAttr(theme.name)}"><span class="ax-theme-variant-preview"><span class="ax-theme-variant-label">DARK</span><span class="ax-theme-swatches"><i style="background:${escapeAttr(dark.bg)}"></i><i style="background:${escapeAttr(dark.surface)}"></i><i style="background:${escapeAttr(dark.primary)}"></i></span><span class="ax-theme-variant-label">LIGHT</span><span class="ax-theme-swatches"><i style="background:${escapeAttr(light.bg)}"></i><i style="background:${escapeAttr(light.surface)}"></i><i style="background:${escapeAttr(light.primary)}"></i></span></span><span class="ax-theme-name">${escapeAttr(theme.name)}</span><span class="ax-theme-check" aria-hidden="true">✓</span></button><button type="button" class="ax-theme-card-delete" data-delete-custom-theme-id="${escapeAttr(theme.id)}" aria-label="Delete ${escapeAttr(theme.name)}" title="Delete theme">×</button></div>`;
    }).join('') : '<p class="ax-custom-theme-empty">No custom themes yet. Create one to customize the entire Archive X interface.</p>';
    host.querySelectorAll('[data-custom-theme-id]').forEach(button => button.addEventListener('click', () => selectCustomTheme(button.dataset.customThemeId)));
    host.querySelectorAll('[data-delete-custom-theme-id]').forEach(button => button.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); deleteCustomThemeById(button.dataset.deleteCustomThemeId); }));
  }
  function restoreTheme(before) {
    if (before && before.custom) {
      const stored = findTheme(before.custom);
      if (stored) { applyPalette(stored, true); return; }
    }
    localStorage.removeItem(ACTIVE_KEY);
    clearTokens();
    if (typeof window.applyArchiveThemePreset === 'function') {
      const preset = before && ['nord','nero-gold','black-essence','pastel-lavender','pastel-mint','minimal-slate','minimal-sand'].includes(before.preset) ? before.preset : 'nord';
      window.applyArchiveThemePreset(preset, before && before.mode || root().dataset.themeMode || 'dark');
    }
  }
  function startEdit(theme, isNew) {
    beforeEdit = getBeforeEdit();
    editing = normalizeTheme(JSON.parse(JSON.stringify(theme))) || JSON.parse(JSON.stringify(theme));
    editingMode = root().dataset.themeMode === 'light' ? 'light' : 'dark';
    if (editing.palettes) editing.palette = { ...(editing.palettes[editingMode] || editing.palette) };
    editingIsNew = !!isNew;
    applyingDraft = true;
    renderEditor(); renderThemeCards();
    applyPalette(editing, false, editingMode);
    applyingDraft = false;
  }
  function selectCustomTheme(id) {
    const theme = findTheme(id);
    if (!theme) return;
    const before = getBeforeEdit();
    localStorage.setItem(ACTIVE_KEY, theme.id);
    const mode = root().dataset.themeMode === 'light' ? 'light' : 'dark';
    const preference = localStorage.getItem('ax_theme_mode') === 'system' ? 'system' : mode;
    applyPalette(theme, true, mode);
    if (preference === 'system') try { localStorage.setItem('ax_theme_mode', 'system'); } catch (_) {}
    if (typeof window._axPersistAppearance === 'function') window._axPersistAppearance({ theme_preset: 'custom', theme_mode: preference }).catch(error => { if (!window.__axThemePersistWarned) { window.__axThemePersistWarned = true; console.warn('Archive X could not persist the theme preference; the local choice remains active.', error); } });
    // The editor retains both variants and edits the currently active one.
    beforeEdit = before;
    editing = JSON.parse(JSON.stringify(theme));
    editingMode = mode; editing.palette = { ...(editing.palettes[mode] || editing.palette) };
    editingIsNew = false;
    renderEditor(); renderThemeCards();
  }
  window.axCreateCustomTheme = function () {
    const mode = root().dataset.themeMode === 'light' ? 'light' : 'dark';
    const current = getCurrentPalette();
    const other = mode === 'dark' ? 'light' : 'dark';
    const palettes = { [mode]: current, [other]: deriveVariantPalette(current, other) };
    const theme = { id: `ax-custom-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`, name: 'My Theme', isDark: mode === 'dark', palette: current, palettes };
    startEdit(theme, true);
  };
  window.axSaveCustomTheme = function () {
    if (!editing) return;
    editing.palettes = editing.palettes || {}; editing.palettes[editingMode] = { ...editing.palette };
    editing.isDark = editingMode === 'dark';
    const normalized = normalizeTheme(editing);
    if (!normalized) { setEditorStatus('One or more color values are invalid. Check the highlighted values.', true); return; }
    const themes = readThemes();
    const index = themes.findIndex(theme => theme.id === normalized.id);
    if (index >= 0) themes[index] = normalized; else themes.push(normalized);
    try { writeThemes(themes.slice(-100)); localStorage.setItem(ACTIVE_KEY, normalized.id); }
    catch (_) { setEditorStatus('Could not save this theme in local storage.', true); return; }
    editing = normalized; editingIsNew = false; beforeEdit = { custom: normalized.id, preset: 'custom', mode: normalized.isDark ? 'dark' : 'light' };
    applyPalette(normalized, true, editingMode); renderEditor(); renderThemeCards();
    setEditorStatus('Theme saved and applied across Archive X.');
    const status = document.getElementById('ax-appearance-status'); if (status) status.textContent = `Custom theme “${normalized.name}” saved and applied across Archive X.`;
  };
  window.axCancelCustomTheme = function () {
    const before = beforeEdit;
    editing = null; editingIsNew = false; beforeEdit = null;
    const panel = document.getElementById('ax-custom-theme-editor'); if (panel) panel.hidden = true;
    restoreTheme(before); renderThemeCards();
    const status = document.getElementById('ax-appearance-status'); if (status) status.textContent = 'Custom theme edit cancelled.';
  };
  async function deleteCustomThemeById(id) {
    const theme = findTheme(id);
    if (!theme) return;
    if (!(await window.axConfirm(`Delete the custom theme “${theme.name}”? This cannot be undone.`, { title: 'Delete theme?', okLabel: 'Delete theme', confirmLabel: 'Delete theme', danger: true }))) return;
    const themes = readThemes().filter(item => item.id !== id);
    try { writeThemes(themes); } catch (_) { setEditorStatus('Could not update saved themes.', true); return; }
    const wasActive = activeId() === id;
    if (wasActive) {
      localStorage.removeItem(ACTIVE_KEY);
      const fallbackMode = root().dataset.themeMode === 'light' ? 'light' : 'dark';
      if (typeof window.applyArchiveThemePreset === 'function') window.applyArchiveThemePreset('nord', fallbackMode);
    }
    if (editing && editing.id === id) {
      editing = null; editingIsNew = false; beforeEdit = null;
      const panel = document.getElementById('ax-custom-theme-editor'); if (panel) panel.hidden = true;
    }
    renderThemeCards();
    const status = document.getElementById('ax-appearance-status'); if (status) status.textContent = 'Custom theme deleted.';
  }
  window.axDeleteCustomTheme = async function () {
    if (!editing || editingIsNew) return;
    await deleteCustomThemeById(editing.id);
  };
  window.axCustomThemeEditorInput = function (event) {
    if (event.target && (event.target.id === 'ax-custom-theme-name' || event.target.dataset.customColor || event.target.dataset.customBase)) updateDraftFromInput(event.target);
  };


  // ------------------------------------------------------------------
  // SECTION: Integration and init
  // Wraps app functions and starts the module.
  // ------------------------------------------------------------------
  // Wrap preset changes so explicitly choosing a built-in theme exits custom mode,
  // while reload/save operations restore the user-selected custom palette afterward.
  function wrap(name, wrapper) {
    const original = window[name];
    if (typeof original !== 'function' || original.__axCustomWrapped) return;
    const wrapped = wrapper(original);
    wrapped.__axCustomWrapped = true;
    window[name] = wrapped;
  }
  wrap('applyArchiveThemePreset', original => function (preset) {
    clearTokens();
    return original.apply(this, arguments);
  });
  wrap('selectArchiveThemePreset', original => function (preset) {
    editing = null; editingIsNew = false; beforeEdit = null; localStorage.removeItem(ACTIVE_KEY); clearTokens();
    const editor = document.getElementById('ax-custom-theme-editor'); if (editor) editor.hidden = true;
    renderThemeCards();
    return original.apply(this, arguments);
  });
  wrap('onThemeModeChange', original => function () {
    const mode = document.getElementById('v55-theme-mode')?.value === 'light' ? 'light' : 'dark';
    if (activeId() && window.axApplyCustomThemeMode && window.axApplyCustomThemeMode(mode)) return true;
    editing = null; editingIsNew = false; beforeEdit = null; localStorage.removeItem(ACTIVE_KEY); clearTokens();
    const editor = document.getElementById('ax-custom-theme-editor'); if (editor) editor.hidden = true;
    renderThemeCards();
    return original.apply(this, arguments);
  });
  ['loadExperienceSettings','saveExperienceSettings'].forEach(name => wrap(name, original => async function () {
    const result = await original.apply(this, arguments);
    // A draft being edited takes precedence over a background preferences refresh.
    if (editing) applyPalette(editing, false);
    else {
      const active = findTheme(activeId());
      if (active) applyPalette(active, true);
    }
    renderThemeCards();
    return result;
  }));

  window.axApplyCustomThemeMode = function (mode) {
    mode = mode === 'light' ? 'light' : 'dark';
    const id = activeId();
    const stored = id ? findTheme(id) : null;
    // A new draft is also a live custom-theme preview. Let the sidebar switch
    // change that draft's variant without persisting its unsaved ID as active.
    const draftIsApplied = !!(editing && root().dataset.themePreset === 'custom' && (editingIsNew || editing.id === id));
    if (draftIsApplied) {
      editing.palettes = editing.palettes || {};
      editing.palettes[editingMode] = { ...editing.palette };
      editingMode = mode;
      editing.palette = { ...(editing.palettes[mode] || deriveVariantPalette(editing.palette, mode)) };
      editing.palettes[mode] = { ...editing.palette };
      editing.isDark = mode === 'dark';
      applyPalette(editing, !editingIsNew, mode);
      renderEditor();
      return true;
    }
    if (!stored) return false;
    applyPalette(stored, true, mode);
    return true;
  };

  function init() {
    const name = document.getElementById('ax-custom-theme-name');
    if (name) name.addEventListener('input', () => updateDraftFromInput(name));
    document.querySelectorAll('[data-custom-base]').forEach(button => button.addEventListener('click', () => {
      if (!editing) return;
      updateDraftFromInput({ dataset: { customBase: button.dataset.customBase } });
      renderEditor();
    }));
    const active = findTheme(activeId());
    if (active) applyPalette(active, true, root().dataset.themeMode === 'light' ? 'light' : 'dark');
    renderThemeCards();
    // Async server settings can finish after page scripts execute; reassert the local custom theme.
    window.setTimeout(() => {
      if (!editing) { const current = findTheme(activeId()); if (current) applyPalette(current, true, root().dataset.themeMode === 'light' ? 'light' : 'dark'); }
    }, 0);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
})();
