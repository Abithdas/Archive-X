/* ============================================================================
 * FILE: js/141-ax2-settings.js
 * AREA: Settings
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X Step 7 — Settings information architecture only.
   Reuses the original live controls; no settings save/load or API logic is changed. */
(function () {
  'use strict';
  const byId = (id) => document.getElementById(id);
  const isPhone = () => window.matchMedia('(max-width: 599px)').matches;

  function initArchiveSettingsStep7() {
    const root = byId('vs');
    const main = root && root.querySelector('.settings-v54-main');
    const banner = byId('settings-v54-overview');
    if (!root || !main || !banner || root.dataset.axSettingsStep7Ready === '1') return;
    root.dataset.axSettingsStep7Ready = '1';

    const oldWorkspace = root.querySelector('.ax-settings-workspace');
    const statusPills = banner.querySelector('.settings-v54-state');
    const title = banner.querySelector('.title');
    const subtitle = banner.querySelector('.sub');
    if (title) title.textContent = 'Settings';
    if (subtitle) subtitle.textContent = 'Settings are validated before they are applied';
    banner.classList.add('ax2-page-head');
    if (title && !banner.querySelector('.ax2-settings-eyebrow')) {
      const eyebrow = document.createElement('div');
      eyebrow.className = 'ax2-settings-eyebrow';
      eyebrow.textContent = 'CONFIGURATION';
      title.parentNode.insertBefore(eyebrow, title);
    }

    const nodes = {
      status: byId('settings-v54-status'),
      telegram: byId('settings-v54-telegram'),
      metadata: byId('settings-v54-metadata'),
      experience: byId('settings-v55-experience'),
      storage: byId('settings-v55-storage'),
      backup: byId('settings-v55-backup'),
      security: byId('settings-v54-security'),
      browser: byId('settings-browser-compat'),
      hardware: byId('settings-hardware-adaptive'),
      actions: byId('settings-v54-actions'),
      themeMode: byId('v55-theme-mode')
    };
    const appearanceSections = [...root.querySelectorAll('.ax-appearance-section')].filter((node) => node.closest('#vs') === root);
    const legacyCaption = byId('settings-config-caption');
    if (legacyCaption) legacyCaption.remove();

    const workspace = document.createElement('div');
    workspace.className = 'ax2-settings-workspace';
    workspace.setAttribute('aria-label', 'Settings categories');
    const content = document.createElement('div');
    content.className = 'ax2-settings-content';
    content.setAttribute('aria-label', 'Settings section content');
    const nav = document.createElement('nav');
    nav.className = 'ax2-settings-nav';
    nav.setAttribute('aria-label', 'Settings categories');
    const navList = document.createElement('div');
    navList.className = 'ax2-settings-nav-list';
    nav.appendChild(navList);
    const navActions = document.createElement('div');
    navActions.className = 'ax2-settings-nav-actions';
    navActions.setAttribute('aria-label', 'Configuration actions');
    nav.appendChild(navActions);

    const definitions = [
      { id: 'overview', label: 'Overview', detail: 'Connection and system status', icon: 'ax-grid', description: 'A quick view of connection status and system health.', nodes: [nodes.status], overview: true },
      { id: 'telegram', label: 'Telegram', detail: 'API, session and sign-in', icon: 'ax-brand-telegram', description: 'Manage Telegram credentials, sign-in and local session.', nodes: [nodes.telegram] },
      { id: 'metadata', label: 'Metadata', detail: 'ThePornDB, StashDB, scan window', icon: 'ax-database', description: 'Configure provider keys, scan windows and local enrichment.', nodes: [nodes.metadata] },
      { id: 'experience', label: 'Experience', detail: 'Autoplay, resume, seek preview', icon: 'ax-play', description: 'Configure playback preferences and discovery behavior.', nodes: [nodes.experience] },
      { id: 'appearance', label: 'Appearance', detail: 'Zoom, themes, custom themes', icon: 'ax-sparkle', description: 'Customize interface scale, theme presets and custom themes.', nodes: appearanceSections.concat(nodes.themeMode ? [nodes.themeMode] : []) },
      { id: 'storage', label: 'Storage & Backup', detail: 'Cache usage, data, backup, restore', icon: 'ax-folders', description: 'Manage video cache usage, the data folder, backup history and restore staging.', nodes: [nodes.storage, nodes.backup] },
      { id: 'security', label: 'Security', detail: 'Dashboard login', icon: 'ax-lock', description: 'Manage the Archive X dashboard login and session credentials.', nodes: [nodes.security] },
      { id: 'system', label: 'System & network', detail: 'Hardware, browser, speed test', icon: 'ax-gauge', description: 'Review browser compatibility, hardware and network testing.', nodes: [nodes.browser, nodes.hardware] }
    ];
    const sections = new Map();

    definitions.forEach((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ax2-settings-nav-item';
      button.dataset.settingsCategory = item.id;
      button.setAttribute('aria-controls', `ax2-settings-panel-${item.id}`);
      button.setAttribute('aria-current', 'false');
      button.innerHTML = `<span class="ax2-settings-nav-icon"><svg class="ax-icon" aria-hidden="true"><use href="#${item.icon}"></use></svg></span><span class="ax2-settings-nav-copy"><b>${item.label}</b><small>${item.detail}</small></span><span class="ax2-settings-nav-chevron" aria-hidden="true">›</span>`;
      navList.appendChild(button);

      const panel = document.createElement('section');
      panel.className = 'ax2-settings-panel';
      panel.dataset.settingsPanel = item.id;
      panel.id = `ax2-settings-panel-${item.id}`;
      panel.setAttribute('aria-labelledby', `ax2-settings-title-${item.id}`);
      const head = document.createElement('header');
      head.className = 'ax2-settings-panel-head';
      head.innerHTML = `<div><div class="ax2-settings-eyebrow">CONFIGURATION</div><h2 id="ax2-settings-title-${item.id}">${item.label}</h2><p>${item.description}</p></div><span class="ax2-settings-count">${String(sections.size + 1).padStart(2, '0')} <i>/ 08</i></span>`;
      const body = document.createElement('div');
      body.className = 'ax2-settings-panel-body';
      panel.append(head, body);
      content.appendChild(panel);
      sections.set(item.id, { definition: item, button, panel, body });
      button.addEventListener('click', () => openCategory(item.id, true));
    });

    // Overview gets a single status card and the existing state values, without cloning any nodes.
    const overviewBody = sections.get('overview').body;
    const statusCard = document.createElement('div');
    statusCard.className = 'ax2-settings-status-card';
    const statusTitle = document.createElement('div');
    statusTitle.className = 'ax2-settings-status-title';
    statusTitle.textContent = 'Connection status';
    const pillWrap = document.createElement('div');
    pillWrap.className = 'ax2-settings-status-pills';
    statusCard.append(statusTitle, pillWrap);
    if (statusPills) pillWrap.appendChild(statusPills);
    overviewBody.appendChild(statusCard);
    const caption = document.createElement('div');
    caption.id = 'settings-config-caption';
    caption.className = 'ax2-settings-footnote';
    caption.textContent = 'Configuration changes are applied through the existing settings handlers; live jobs remain in Operations.';
    overviewBody.appendChild(caption);
    sections.get('overview').body.appendChild(nodes.status);

    // All other controls are moved, never cloned, preserving IDs and inline handlers.
    definitions.filter((item) => !item.overview).forEach((item) => {
      const target = sections.get(item.id).body;
      item.nodes.forEach((node) => { if (node) target.appendChild(node); });
    });
    if (nodes.actions) navActions.appendChild(nodes.actions);

    // Dark/Light segmented control uses the existing hidden select and its onchange handler.
    const themeHeading = sections.get('appearance').body.querySelector('.ax-themes-section .ax-appearance-heading');
    const themeModeSelect = nodes.themeMode;
    if (themeHeading && themeModeSelect && !themeHeading.querySelector('.ax2-settings-theme-mode')) {
      const modeControl = document.createElement('div');
      modeControl.className = 'ax2-settings-theme-mode';
      modeControl.setAttribute('role', 'group');
      modeControl.setAttribute('aria-label', 'Color mode');
      modeControl.innerHTML = '<button type="button" data-settings-theme-mode="dark" aria-pressed="false">Dark</button><button type="button" data-settings-theme-mode="light" aria-pressed="false">Light</button>';
      themeHeading.appendChild(modeControl);
      const syncThemeMode = () => {
        const mode = themeModeSelect.value === 'light' || document.documentElement.dataset.themeMode === 'light' ? 'light' : 'dark';
        modeControl.querySelectorAll('[data-settings-theme-mode]').forEach((button) => {
          const active = button.dataset.settingsThemeMode === mode;
          button.classList.toggle('is-active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
      };
      modeControl.querySelectorAll('[data-settings-theme-mode]').forEach((button) => {
        button.addEventListener('click', () => {
          const mode = button.dataset.settingsThemeMode;
          themeModeSelect.value = mode;
          themeModeSelect.dispatchEvent(new Event('change', { bubbles: true }));
          syncThemeMode();
        });
      });
      themeModeSelect.addEventListener('change', syncThemeMode);
      if ('MutationObserver' in window) {
        const themeObserver = new MutationObserver(syncThemeMode);
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme-mode', 'data-theme', 'data-theme-preset'] });
      }
      syncThemeMode();
    }

    // Remove only the empty wrappers from the previous settings layout pass.
    if (oldWorkspace) oldWorkspace.remove();
    const oldAreas = main.querySelector('.settings-area-grid');
    if (oldAreas) oldAreas.remove();
    const overviewGrid = main.querySelector('.settings-overview-grid');
    if (overviewGrid && !overviewGrid.querySelector('#settings-v54-status,#settings-browser-compat,#settings-hardware-adaptive')) overviewGrid.remove();
    const oldG2 = main.querySelector('.g2');
    if (oldG2 && !oldG2.querySelector('#settings-v54-telegram,#settings-v54-metadata,#settings-v54-security')) oldG2.remove();

    const mobileBack = document.createElement('div');
    mobileBack.className = 'ax2-settings-mobile-back';
    mobileBack.innerHTML = '<button type="button" aria-label="Back to Settings">‹</button><strong>Settings</strong>';
    const mobileBackButton = mobileBack.querySelector('button');
    mobileBackButton.addEventListener('click', () => goHome(true));
    content.insertBefore(mobileBack, content.firstChild);
    workspace.append(nav, content);
    banner.insertAdjacentElement('afterend', workspace);
    root.dataset.settingsPhonePage = 'home';

    function activate(id) {
      const normalized = sections.has(id) ? id : 'overview';
      sections.forEach(({ button, panel }, key) => {
        const active = key === normalized;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-current', active ? 'page' : 'false');
        panel.classList.toggle('is-active', active);
        panel.hidden = !active;
      });
      const active = sections.get(normalized);
      if (mobileBack) {
        mobileBack.hidden = normalized === 'overview';
        const label = mobileBack.querySelector('strong');
        if (label) label.textContent = active.definition.label;
      }
      return normalized;
    }
    function openCategory(id, pushHistory) {
      const normalized = activate(id);
      if (isPhone()) {
        if (normalized === 'overview') {
          root.dataset.settingsPhonePage = 'home';
          activate('overview');
          return;
        }
        const current = root.dataset.settingsPhonePage || 'home';
        if (pushHistory && current !== normalized) {
          const state = history.state && typeof history.state === 'object' ? history.state : {};
          try {
            if (!state.__axSettingsStep7Home && !state.__axSettingsStep7Category) {
              history.pushState({ ...state, __axSettingsStep7Home: true }, '', location.href);
            }
            const homeState = history.state && typeof history.state === 'object' ? history.state : {};
            history.pushState({ ...homeState, __axSettingsStep7Home: false, __axSettingsStep7Category: normalized }, '', location.href);
          } catch (_) {}
        }
        root.dataset.settingsPhonePage = normalized;
        const target = sections.get(normalized).panel;
        if (target) target.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        return;
      }
      root.dataset.settingsPhonePage = 'home';
      const target = sections.get(normalized).panel;
      if (target) target.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }
    function goHome(useHistory) {
      if (useHistory && history.state && history.state.__axSettingsStep7Category) {
        history.back();
        return;
      }
      root.dataset.settingsPhonePage = 'home';
      activate('overview');
      const top = banner.getBoundingClientRect().top + window.scrollY - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ax2-top')) || 60) - 12;
      window.scrollTo({ top: Math.max(0, top), behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }
    function resolveCategory(key) {
      const value = String(key || 'overview').trim().toLowerCase().replace(/&/g, 'and').replace(/[ _]+/g, '-');
      const aliases = {
        'storage-backup': 'storage', 'storage-and-backup': 'storage', 'backup': 'storage', 'backup-restore': 'storage',
        'playback': 'experience', 'playback-experience': 'experience', 'settings': 'overview',
        'system-network': 'system', 'system-and-network': 'system', 'system-diagnostics': 'system',
        'appearance-themes': 'appearance', 'telegram-settings': 'telegram', 'metadata-providers': 'metadata'
      };
      return sections.has(value) ? value : (aliases[value] || 'overview');
    }
    window.focusSettings = function (key) { openCategory(resolveCategory(key), false); };
    window.axSettingsShowCategory = function (key) { openCategory(resolveCategory(key), false); };
    window.axSettingsGoHome = () => goHome(false);

    // Keyboard navigation through the left nav and phone home list.
    nav.addEventListener('keydown', (event) => {
      if (!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
      const buttons = [...navList.querySelectorAll('button')].filter((button) => getComputedStyle(button).display !== 'none');
      const current = buttons.indexOf(document.activeElement);
      if (current < 0 || !buttons.length) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
      openCategory(buttons[next].dataset.settingsCategory, true);
    });

    // Preserve the Settings-only browser back behavior without altering the global _navPush router.
    window.addEventListener('popstate', (event) => {
      if (event.state && event.state.__axSettingsStep7Category) {
        root.dataset.settingsPhonePage = event.state.__axSettingsStep7Category;
        activate(event.state.__axSettingsStep7Category);
      } else if (event.state && event.state.__axSettingsStep7Home) {
        root.dataset.settingsPhonePage = 'home';
        activate('overview');
      }
    });

    // Desktop scroll-spy. Phone navigation intentionally remains a home/sub-page flow.
    let observer;
    function connectScrollSpy() {
      if (observer) observer.disconnect();
      if (isPhone() || !('IntersectionObserver' in window)) return;
      observer = new IntersectionObserver((entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a,b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length) activate(visible[0].target.dataset.settingsPanel);
      }, { root: null, rootMargin: '-12% 0px -68% 0px', threshold: [0, .05, .2] });
      sections.forEach(({ panel }) => observer.observe(panel));
    }
    window.addEventListener('resize', connectScrollSpy, { passive: true });
    connectScrollSpy();
    // Reference desktop opens the Appearance panel; phones open the Settings home overview.
    activate(isPhone() ? 'overview' : 'appearance');

    // Convert only existing real checkboxes into visual switches; the input objects and onchange handlers remain intact.
    ['v55-autoplay','v55-autonext','v55-resume-prompt','v55-live-seek-preview','ax-scan-window-enabled','backup-enabled'].forEach((id) => {
      const input = byId(id);
      if (!input || input.dataset.ax2SwitchReady === '1') return;
      const label = input.closest('label');
      if (!label) return;
      label.classList.add('ax2-switch-row');
      input.classList.add('ax2-switch');
      input.dataset.ax2SwitchReady = '1';
      const contentNodes = [...label.childNodes].filter((node) => node !== input);
      const text = document.createElement('span');
      text.className = 'ax2-switch-label';
      contentNodes.forEach((node) => text.appendChild(node));
      label.insertBefore(text, input);
      label.appendChild(input);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initArchiveSettingsStep7, { once: true });
  else initArchiveSettingsStep7();
})();
