/* ============================================================================
 * FILE: js/114-theme-engine-enhancements.js
 * AREA: Theming
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* v29.4.259 — paired built-in themes, recoverable preset removal, and sidebar mode switch. */
(function () {
  'use strict';
  const HIDDEN_KEY = 'ax_hidden_builtin_themes_v1';
  const families = [
    { id:'nord', name:'Nord', dark:['#2e3440','#3b4252','#88c0d0'], light:['#eceff4','#ffffff','#527ba5'] },
    { id:'nero-gold', name:'Nero Gold', dark:['#131313','#262625','#efd395'], light:['#f7f4eb','#ffffff','#8d6a18'] },
    { id:'black-essence', name:'Black Essence', dark:['#030303','#141414','#d1d1d1'], light:['#f2f2f2','#ffffff','#343434'] },
    { id:'pastel-lavender', name:'Pastel Lavender', dark:['#231f2e','#2d2839','#cbb8f5'], light:['#f6f1fd','#ffffff','#7a5fc0'] },
    { id:'pastel-mint', name:'Pastel Mint', dark:['#1b2625','#243230','#a8e6cf'], light:['#eefaf5','#ffffff','#2f8a6a'] },
    { id:'minimal-slate', name:'Minimal Slate', dark:['#1a1d21','#22262b','#8fa3b8'], light:['#f4f6f8','#ffffff','#44586c'] },
    { id:'minimal-sand', name:'Minimal Sand', dark:['#1a1815','#231f1b','#c4a484'], light:['#f8f5f0','#ffffff','#86644a'] }
  ];
  const $ = id => document.getElementById(id);
  const root = () => document.documentElement;
  function aliases(id) { return ({'nobuf-dark':'nobuf','nobuf-light':'nobuf','solarized-light':'solarized'})[id] || id; }
  function hiddenFamilies() {
    try { const value = JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]'); return new Set(Array.isArray(value) ? value.filter(id => families.some(f => f.id === id)) : []); }
    catch (_) { return new Set(); }
  }
  function writeHidden(set) { try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(Array.from(set))); } catch (_) {} }
  function swatches(colors) { return `<span class="ax-theme-variant-swatches">${colors.map(color => `<i style="background:${color}" aria-hidden="true"></i>`).join('')}</span>`; }
  function renderBuiltinThemeCards() {
    const host = $('ax-theme-presets');
    if (!host) return;
    const hidden = hiddenFamilies();
    const rawPreset = aliases(root().dataset.themePreset || localStorage.getItem('ax_theme_preset') || 'nord');
    const activePreset = rawPreset === 'custom' ? 'custom' : (families.some(theme => theme.id === rawPreset) ? rawPreset : 'nord');
    host.innerHTML = families.filter(theme => !hidden.has(theme.id)).map(theme => {
      const active = activePreset === theme.id;
      return `<div class="ax-theme-family-card"><button type="button" class="ax-theme-option${active ? ' active' : ''}" data-theme-option="${theme.id}" aria-pressed="${active ? 'true' : 'false'}" title="${theme.name}"><span class="ax-theme-variant-preview"><span class="ax-theme-variant-block"><span class="ax-theme-variant-label">Dark</span>${swatches(theme.dark)}</span><span class="ax-theme-variant-block"><span class="ax-theme-variant-label">Light</span>${swatches(theme.light)}</span></span><span class="ax-theme-name">${theme.name}</span><span class="ax-theme-check" aria-hidden="true">✓</span></button><button type="button" class="ax-theme-card-delete" data-delete-builtin-theme="${theme.id}" aria-label="Delete ${theme.name} theme" title="Delete theme">×</button></div>`;
    }).join('') || '<p class="ax-custom-theme-empty">All built-in themes are removed from the picker. Restore defaults to bring them back.</p>';
    host.querySelectorAll('[data-theme-option]').forEach(button => button.addEventListener('click', () => {
      if (typeof window.selectArchiveThemePreset === 'function') window.selectArchiveThemePreset(button.dataset.themeOption);
    }));
    host.querySelectorAll('[data-delete-builtin-theme]').forEach(button => button.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation(); window.axDeleteBuiltinTheme(button.dataset.deleteBuiltinTheme);
    }));
    const restore = $('ax-restore-builtin-themes');
    if (restore) restore.hidden = hidden.size === 0;
  }
  function syncThemeModeToggle() {
    const light = root().dataset.theme === 'light';
    ['ax-sidebar-theme-toggle', 'ax-mobile-theme-toggle'].forEach(id => {
      const button = $(id);
      if (!button) return;
      button.setAttribute('aria-checked', light ? 'true' : 'false');
      button.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
      button.title = light ? 'Switch to dark mode' : 'Switch to light mode';
    });
  }
  window.axSyncThemeModeToggle = syncThemeModeToggle;
  window.axDeleteBuiltinTheme = async function (id) {
    id = aliases(String(id || ''));
    const theme = families.find(item => item.id === id);
    if (!theme) return;
    if (!(await window.axConfirm(`Remove the built-in theme “${theme.name}” from the theme picker? You can restore it later.`, { title: 'Remove theme?', okLabel: 'Remove theme', confirmLabel: 'Remove theme', danger: true }))) return;
    const hidden = hiddenFamilies(); hidden.add(id); writeHidden(hidden);
    const currentRaw = aliases(root().dataset.themePreset || localStorage.getItem('ax_theme_preset') || 'nord');
    const current = currentRaw === 'custom' ? 'custom' : (families.some(item => item.id === currentRaw) ? currentRaw : 'nord');
    if (current === id) {
      let fallback = families.find(item => !hidden.has(item.id));
      if (!fallback) { hidden.delete('nord'); writeHidden(hidden); fallback = families.find(item => item.id === 'nord') || families[0]; }
      if (typeof window.selectArchiveThemePreset === 'function') window.selectArchiveThemePreset(fallback.id);
      else if (typeof window.applyArchiveThemePreset === 'function') window.applyArchiveThemePreset(fallback.id, root().dataset.theme === 'light' ? 'light' : 'dark');
    }
    renderBuiltinThemeCards();
  };
  window.axRestoreBuiltinThemes = function () {
    writeHidden(new Set()); renderBuiltinThemeCards();
    const status = $('ax-appearance-status'); if (status) status.textContent = 'All built-in themes restored.';
  };
  window.axToggleThemeMode = async function () {
    const nextMode = root().dataset.theme === 'light' ? 'dark' : 'light';
    let applied = false;
    if (root().dataset.themePreset === 'custom' && typeof window.axApplyCustomThemeMode === 'function') applied = window.axApplyCustomThemeMode(nextMode);
    if (!applied) {
      const rawPreset = aliases(root().dataset.themePreset || localStorage.getItem('ax_theme_preset') || 'nord');
      const preset = rawPreset === 'custom' ? 'custom' : (families.some(item => item.id === rawPreset) ? rawPreset : 'nord');
      if (typeof window.applyArchiveThemePreset === 'function') window.applyArchiveThemePreset(preset, nextMode);
      else return;
    }
    try { localStorage.setItem('ax_theme_mode', nextMode); } catch (_) {}
    syncThemeModeToggle();
    renderBuiltinThemeCards();
    const status = $('ax-appearance-status');
    if (status) status.textContent = `${root().dataset.themePreset === 'custom' ? 'Custom theme' : (families.find(t => t.id === aliases(root().dataset.themePreset))?.name || 'Theme')} switched to ${nextMode} mode.`;
    try {
      if (typeof window._axPersistAppearance === 'function') {
        await window._axPersistAppearance({ theme_preset: root().dataset.themePreset === 'custom' ? 'custom' : aliases(root().dataset.themePreset), theme_mode: nextMode });
      }
    } catch (error) {
      if (status) status.textContent += ' Preference saved locally; server sync failed.';
      if (!window.__axThemePersistWarned) { window.__axThemePersistWarned = true; console.warn('Archive X could not persist the theme preference; the local choice remains active.', error); }
    }
  };
  function init() {
    const storedMode = (() => { try { return localStorage.getItem('ax_theme_mode') || ''; } catch (_) { return ''; } })();
    const mode = storedMode === 'system' ? (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : (root().dataset.theme === 'light' ? 'light' : 'dark');
    if (root().dataset.themePreset !== 'custom') {
      const rawPreset = aliases(root().dataset.themePreset || localStorage.getItem('ax_theme_preset') || 'nord');
      root().dataset.themePreset = families.some(item => item.id === rawPreset) ? rawPreset : 'nord';
    }
    renderBuiltinThemeCards(); syncThemeModeToggle();
    // If an old installation had a separate light/dark preset, normalize it to a theme family.
    try { if (root().dataset.themePreset !== 'custom') localStorage.setItem('ax_theme_preset', root().dataset.themePreset || 'nord'); localStorage.setItem('ax_theme_mode', storedMode === 'system' ? 'system' : mode); } catch (_) {}
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
})();
