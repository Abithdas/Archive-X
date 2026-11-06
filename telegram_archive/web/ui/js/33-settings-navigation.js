/* ============================================================================
 * FILE: js/33-settings-navigation.js
 * AREA: Shell and navigation
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/*
 * Archive X settings information architecture.
 * Presentation-only DOM composition: reuses the original setting nodes,
 * input IDs, inline handlers and API routes. Does not touch playback runtime.
 */
(function () {
  'use strict';

  function initArchiveSettingsNavigation() {
    const root = document.getElementById('vs');
    const main = root && root.querySelector('.settings-v54-main');
    const banner = document.getElementById('settings-v54-overview');
    if (!root || !main || !banner || root.dataset.axSettingsNavigationReady === '1') return;
    root.dataset.axSettingsNavigationReady = '1';

    const remove = (el) => { if (el && el.parentNode) el.parentNode.removeChild(el); };
    const byId = (id) => document.getElementById(id);
    const move = (el, target) => { if (el && target) target.appendChild(el); };

    const oldAreaCards = main.querySelector('.settings-area-grid');
    const oldCaption = byId('settings-config-caption');
    const overviewGrid = main.querySelector('.settings-overview-grid');
    const legacyTwoColumn = main.querySelector('.g2');

    // Lift existing sections out of the legacy multi-column wrappers. Their IDs
    // and controls remain stable, so existing commands and load/save routines work.
    const status = byId('settings-v54-status');
    const browserCompat = byId('settings-browser-compat');
    const hardware = byId('settings-hardware-adaptive');
    const telegram = byId('settings-v54-telegram');
    const metadata = byId('settings-v54-metadata');
    const security = byId('settings-v54-security');
    const experience = byId('settings-v55-experience');
    const storage = byId('settings-v55-storage');
    const backup = byId('settings-v55-backup');
    const configActions = byId('settings-v54-actions');

    // Appearance controls currently live inside the Experience section. Keep
    // them as the same DOM nodes while giving them a dedicated category panel.
    const appearanceDensity = experience && experience.querySelector('.ax-appearance-section');
    const appearanceThemes = experience && experience.querySelector('.ax-themes-section');
    const appearanceLegacyMode = byId('v55-theme-mode');

    const workspace = document.createElement('div');
    workspace.className = 'ax-settings-workspace';
    workspace.setAttribute('aria-label', 'Settings categories');
    banner.insertAdjacentElement('afterend', workspace);

    const nav = document.createElement('nav');
    nav.className = 'ax-settings-nav';
    nav.setAttribute('aria-label', 'Settings categories');
    nav.setAttribute('role', 'tablist');
    nav.setAttribute('aria-orientation', 'vertical');

    const content = document.createElement('div');
    content.className = 'ax-settings-content';
    content.setAttribute('aria-live', 'polite');
    workspace.append(nav, content);

    const categories = [
      { id: 'overview', label: 'Overview', detail: 'System status and quick checks', icon: 'ax-circle', nodes: [status], heading: 'Overview', description: 'A quick view of Archive X health and connection status.' },
      { id: 'appearance', label: 'Appearance', detail: 'Themes and library density', icon: 'ax-grid', nodes: [], heading: 'Appearance', description: 'Personalize colors, density and the visual feel of your library.' },
      { id: 'playback', label: 'Playback', detail: 'Playback and discovery behavior', icon: 'ax-play', nodes: [experience], heading: 'Playback & experience', description: 'Configure playback preferences and discovery behavior.' },
      { id: 'telegram', label: 'Telegram', detail: 'API, account and session', icon: 'ax-send', nodes: [telegram], heading: 'Telegram', description: 'Manage Telegram API credentials and account access.' },
      { id: 'metadata', label: 'Metadata', detail: 'Providers and enrichment', icon: 'ax-picture', nodes: [metadata], heading: 'Metadata providers', description: 'Configure provider credentials and inspect enrichment status.' },
      { id: 'security', label: 'Security', detail: 'Dashboard login', icon: 'ax-alert', nodes: [security], heading: 'Security', description: 'Manage the Archive X dashboard login.' },
      { id: 'storage', label: 'Storage & Backup', detail: 'Cache usage, data, backup and restore', icon: 'ax-transfer', nodes: [storage, backup], heading: 'Storage & backup', description: 'Manage video cache usage, application data, backup schedules and restore operations.' },
      { id: 'system', label: 'System', detail: 'Compatibility and diagnostics', icon: 'ax-gear', nodes: [browserCompat, hardware, configActions], heading: 'System & diagnostics', description: 'Review compatibility, hardware detection and configuration health checks.' }
    ];

    const panels = new Map();
    categories.forEach((category, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ax-settings-nav-item';
      button.id = `ax-settings-tab-${category.id}`;
      button.dataset.settingsCategory = category.id;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', `ax-settings-panel-${category.id}`);
      button.setAttribute('aria-selected', 'false');
      button.tabIndex = -1;
      button.innerHTML = `<span class="ax-settings-nav-icon"><svg class="ax-icon" aria-hidden="true"><use href="#${category.icon}"></use></svg></span><span class="ax-settings-nav-copy"><b>${category.label}</b><small>${category.detail}</small></span><span class="ax-settings-nav-chevron" aria-hidden="true">›</span>`;
      button.addEventListener('click', () => showCategory(category.id, true));
      button.addEventListener('keydown', (event) => {
        const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const buttons = [...nav.querySelectorAll('[role="tab"]')];
        let next = index;
        if (event.key === 'ArrowDown') next = (index + 1) % buttons.length;
        if (event.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = buttons.length - 1;
        buttons[next].focus();
        showCategory(buttons[next].dataset.settingsCategory, true);
      });
      nav.appendChild(button);

      const panel = document.createElement('section');
      panel.className = 'ax-settings-panel';
      panel.id = `ax-settings-panel-${category.id}`;
      panel.dataset.settingsPanel = category.id;
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', button.id);
      panel.tabIndex = 0;

      const panelHeader = document.createElement('header');
      panelHeader.className = 'ax-settings-panel-head';
      panelHeader.innerHTML = `<div><div class="ax-settings-eyebrow">ARCHIVE X SETTINGS</div><h2>${category.heading}</h2><p>${category.description}</p></div><span class="ax-settings-count">${String(index + 1).padStart(2, '0')} <i>/ ${String(categories.length).padStart(2, '0')}</i></span>`;
      const body = document.createElement('div');
      body.className = 'ax-settings-panel-body';
      panel.append(panelHeader, body);
      content.appendChild(panel);
      panels.set(category.id, { panel, body, button, category });
    });

    // Move the original controls into the new category panels, without cloning.
    categories.forEach((category) => {
      const target = panels.get(category.id).body;
      category.nodes.forEach((node) => move(node, target));
    });
    const appearanceBody = panels.get('appearance').body;
    move(appearanceDensity, appearanceBody);
    move(appearanceThemes, appearanceBody);
    move(appearanceLegacyMode, appearanceBody);
    if (appearanceDensity || appearanceThemes) {
      const statusText = document.createElement('p');
      statusText.className = 'ax-settings-footnote';
      statusText.textContent = 'Appearance changes are saved using Archive X’s existing preference handlers.';
      appearanceBody.appendChild(statusText);
    }

    // Hide the now-empty old wrappers; their child controls have been moved.
    remove(oldAreaCards);
    remove(oldCaption);
    remove(overviewGrid);
    remove(legacyTwoColumn);

    function showCategory(id, persist) {
      const normalized = panels.has(id) ? id : 'overview';
      panels.forEach(({ panel, button }, categoryId) => {
        const active = categoryId === normalized;
        panel.hidden = !active;
        panel.classList.toggle('is-active', active);
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        button.tabIndex = active ? 0 : -1;
      });
      root.dataset.settingsCategory = normalized;
      if (persist) {
        try { localStorage.setItem('archive-x-settings-category', normalized); } catch (_) {}
      }
      // Return the content viewport to the category heading, not the whole page.
      const stage = root.querySelector('.ax-settings-panel.is-active');
      if (stage) stage.scrollTop = 0;
    }

    // Existing overview cards and helper links can keep calling focusSettings().
    window.focusSettings = function (key) {
      const aliases = { 'storage-backup': 'storage', backup: 'storage', 'experience': 'playback', 'overview': 'overview', 'appearance': 'appearance', 'system': 'system' };
      showCategory(aliases[key] || key || 'overview', true);
    };
    window.axSettingsShowCategory = showCategory;

    let initial = 'overview';
    try {
      const saved = localStorage.getItem('archive-x-settings-category');
      if (saved && panels.has(saved)) initial = saved;
    } catch (_) {}
    showCategory(initial, false);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initArchiveSettingsNavigation, { once: true });
  } else {
    initArchiveSettingsNavigation();
  }
})();
