/* ============================================================================
 * FILE: js/109-transfer-metadata-wizards.js
 * AREA: Transfers
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Wizard building blocks
 *   - Review step
 *   - Wizard assembly
 * ============================================================================ */
/* Archive X v29.4.249 — horizontal step-based Transfer and Metadata manager UI.
   Presentation only: keeps the established form IDs and API handlers intact. */
(function () {
  'use strict';


  // ------------------------------------------------------------------
  // SECTION: Wizard building blocks
  // Step bar, panels and navigation.
  // ------------------------------------------------------------------
  const $ = (selector, root = document) => root.querySelector(selector);
  const make = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  };

  function createStepBar(prefix, definitions, activate) {
    const nav = make('div', 'ax-manager-stepper');
    nav.id = `${prefix}-stepper`;
    nav.setAttribute('role', 'tablist');
    nav.setAttribute('aria-label', `${prefix === 'ax-transfer' ? 'Telegram Transfer Manager' : 'Metadata & Assets Manager'} steps`);
    definitions.forEach((item, index) => {
      const step = index + 1;
      const button = make('button', 'ax-manager-step');
      button.type = 'button';
      button.dataset.axManagerStep = String(step);
      button.id = `${prefix}-step-${step}`;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', 'false');
      button.setAttribute('aria-controls', `${prefix}-panel-${step}`);
      button.append(make('span', 'n', String(step)), make('span', 'ax-manager-step-label', item));
      button.addEventListener('click', () => activate(step));
      nav.append(button);
    });
    return nav;
  }

  function createStepPanels(prefix, definitions) {
    const content = make('div', 'ax-manager-step-content');
    const panels = definitions.map((item, index) => {
      const step = index + 1;
      const panel = make('section', 'ax-manager-step-panel');
      panel.dataset.axManagerPanel = String(step);
      panel.id = `${prefix}-panel-${step}`;
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', `${prefix}-step-${step}`);
      panel.hidden = true;
      const head = make('div', 'ax-manager-panel-heading');
      head.append(make('h3', '', item.title), make('p', '', item.description));
      const body = make('div', 'ax-manager-panel-body');
      panel.append(head, body);
      content.append(panel);
      return { panel, body };
    });
    const progress = make('div', 'ax-manager-progress-track');
    progress.setAttribute('role', 'progressbar');
    progress.setAttribute('aria-valuemin', '0');
    progress.setAttribute('aria-valuemax', '100');
    progress.setAttribute('aria-valuenow', '20');
    progress.setAttribute('aria-label', 'Workflow step progress');
    progress.append(make('span', 'ax-manager-progress-fill'));
    return { content, panels, progress };
  }

  function addNavigation(body, prefix, step, total, definitions, activate) {
    const footer = make('footer', 'ax-manager-step-footer');
    if (step > 1) {
      const back = make('button', 'btn');
      back.type = 'button';
      back.textContent = 'Back';
      back.addEventListener('click', () => activate(step - 1));
      footer.append(back);
    } else {
      footer.append(make('span', 'ax-manager-step-footer-spacer'));
    }
    if (step < total) {
      const next = make('button', 'btn pri ax-manager-next');
      next.type = 'button';
      next.textContent = `Next: ${definitions[step].short} →`;
      next.addEventListener('click', () => activate(step + 1));
      footer.append(next);
    } else {
      footer.append(make('span', 'ax-manager-step-end-note', 'Check the settings before starting the job.'));
    }
    body.append(footer);
  }

  function activateStep(card, prefix, step, definitions) {
    const total = definitions.length;
    const current = Math.max(1, Math.min(total, Number(step) || 1));
    card.dataset.axManagerCurrentStep = String(current);
    card.querySelectorAll('.ax-manager-step').forEach(button => {
      const n = Number(button.dataset.axManagerStep);
      button.classList.toggle('is-active', n === current);
      button.classList.toggle('is-done', n < current);
      button.setAttribute('aria-selected', n === current ? 'true' : 'false');
      button.tabIndex = n === current ? 0 : -1;
    });
    card.querySelectorAll('.ax-manager-step-panel').forEach(panel => {
      const isCurrent = Number(panel.dataset.axManagerPanel) === current;
      panel.hidden = !isCurrent;
      panel.classList.toggle('is-active', isCurrent);
    });
    const progress = card.querySelector('.ax-manager-progress-track');
    const fill = card.querySelector('.ax-manager-progress-fill');
    const pct = Math.round(current / total * 100);
    if (fill) fill.style.width = `${pct}%`;
    if (progress) {
      progress.setAttribute('aria-valuenow', String(pct));
      progress.setAttribute('aria-valuetext', `${pct}% complete`);
    }
    if (prefix === 'ax-transfer') renderTransferReview(card);
    if (prefix === 'ax-metadata') renderMetadataReview(card);
  }

  function addSectionLabel(container, title, detail) {
    const box = make('div', 'ax-manager-subsection-heading');
    box.append(make('strong', '', title));
    if (detail) box.append(make('span', '', detail));
    container.append(box);
    return box;
  }


  // ------------------------------------------------------------------
  // SECTION: Review step
  // Builds the review summaries.
  // ------------------------------------------------------------------
  function optionText(id) {
    const control = document.getElementById(id);
    if (!control) return '—';
    if (control.tagName === 'SELECT') return control.selectedOptions?.[0]?.textContent?.trim() || 'Not selected';
    if (control.type === 'checkbox') return control.checked ? 'Yes' : 'No';
    return String(control.value || '').trim() || 'Not entered';
  }

  function reviewRows(host, rows) {
    host.replaceChildren();
    if (!rows.length) {
      host.append(make('p', 'ax-manager-empty-note', 'Select your settings to see a review here.'));
      return;
    }
    const grid = make('div', 'ax-manager-review-grid');
    rows.forEach(([label, value]) => {
      const row = make('div', 'ax-manager-review-row');
      row.append(make('span', 'ax-manager-review-label', label), make('strong', 'ax-manager-review-value', value || '—'));
      grid.append(row);
    });
    host.append(grid);
  }

  function renderTransferReview(card) {
    const host = $('#ax-transfer-review-content', card);
    if (!host) return;
    const local = ($('#u-transfer-source')?.value || 'local') === 'local';
    const rows = [['Source type', local ? 'Local files' : 'Telegram media']];
    if (local) {
      const imageUpload = !!document.getElementById('u-transfer-local-images')?.checked;
      rows.push(['Transfer type', imageUpload ? 'Images · Telegram documents' : 'Videos · preparation and upload']);
      rows.push(['Source folder', optionText('u-transfer-folder')]);
      rows.push(['Destination', optionText('u-transfer-local-dst')]);
      if (!imageUpload) {
        rows.push(['Video orientation', optionText('u-transfer-local-orientation')]);
        rows.push(['Source resolution', optionText('u-transfer-local-resolution')]);
        rows.push(['Oversized video handling', optionText('u-transfer-oversized-policy')]);
      }
      rows.push(['Include subfolders', optionText('u-transfer-subfolders')]);
      rows.push(['Delete source after verified delivery', optionText('u-transfer-delete-source')]);
    } else {
      rows.push(['Source channels', $('#u-tg-channel-summary')?.textContent || 'Selected: 0 channels']);
      rows.push(['Destination', optionText('u-tg-destination')]);
      rows.push(['Source media', optionText('u-tg-source-media')]);
      rows.push(['Destination media', optionText('u-tg-destination-media')]);
      rows.push(['Video orientation', optionText('u-tg-orientation')]);
      rows.push(['Source resolution', optionText('u-tg-resolution')]);
      rows.push(['Check source duplicates', optionText('u-tg-dup-source')]);
      rows.push(['Check destination', optionText('u-tg-dup-destination')]);
      rows.push(['Check Archive X database', optionText('u-tg-dup-db')]);
      rows.push(['Cleanup broken/orphaned', optionText('u-tg-cleanup')]);
    }
    reviewRows(host, rows);
  }

  function selectedChannelNames() {
    return Array.from(document.querySelectorAll('#u-assets-channel-list input[type="checkbox"]:checked'))
      .map(input => input.closest('label')?.querySelector('span')?.textContent?.trim() || '')
      .filter(Boolean);
  }

  function selectedStageNames() {
    return [
      ['u-assets-stage-inventory', 'Inventory'],
      ['u-assets-stage-metadata-entities', 'Metadata & Entity Data'],
      ['u-assets-stage-snapshots', 'Snapshots'],
      ['u-assets-stage-image-scan', 'Image scan · full channel history'],
      ['u-assets-stage-photo-reconcile', 'Reconcile deleted Telegram images · indexed messages only']
    ].filter(([id]) => document.getElementById(id)?.checked).map(([, label]) => label);
  }

  function renderMetadataReview(card) {
    const host = $('#ax-metadata-review-content', card);
    if (!host) return;
    const channels = selectedChannelNames();
    const stages = selectedStageNames();
    reviewRows(host, [
      ['Selected channels', channels.length ? `${channels.length}: ${channels.join(', ')}` : 'None selected'],
      ['Refresh scope', optionText('u-assets-scope-preset')],
      ['Processing stages', stages.length ? stages.join(' → ') : 'None selected'],
      ['Force metadata recheck', optionText('u-assets-force-metadata')],
      ['Force performer/studio recheck', optionText('u-assets-force-entity')],
      ['Reconcile older messages + uploaded captions', optionText('u-assets-full-inventory')],
      ['Reconcile deleted Telegram images', optionText('u-assets-stage-photo-reconcile')],
      ['Progress and logs', 'Live Lane for quick status · Jobs for details']
    ]);
  }

  function transferMode(card) {
    const local = ($('#u-transfer-source')?.value || 'local') === 'local';
    card.dataset.axTransferSource = local ? 'local' : 'telegram';
    renderTransferReview(card);
  }


  // ------------------------------------------------------------------
  // SECTION: Wizard assembly
  // Transfer and Metadata wizards and init.
  // ------------------------------------------------------------------
  function buildTransferWizard() {
    const card = $('#ops-telegram-transfer');
    if (!card || card.dataset.axManagerWizard === 'ready') return;

    const mi = $('.mi', card), title = $('.mn', card);
    const description = $('.md', card), help = $('.ax-transfer-help', card);
    const sourceRow = $('.ax-transfer-source-row', card);
    const localShell = $('#u-transfer-local'), telegramShell = $('#u-transfer-telegram');
    const localFolder = $('.ax-transfer-folder-field', localShell);
    const localDestination = $('.ax-transfer-local-destination-row', localShell);
    const localMediaModeToggle = $('.ax-transfer-media-mode-toggle', localShell);
    const telegramSourceLabel = $(':scope > label', telegramShell);
    const telegramChannelPanel = $(':scope > .pan', telegramShell);
    const telegramDestination = $('.ax-transfer-telegram-destination', telegramShell);
    const advanced = $('.ax-transfer-advanced', telegramShell);
    const actionRow = $('.tg-transfer-actions', card), message = $('#u-transfer-msg');
    if (![title, sourceRow, localFolder, localDestination, telegramChannelPanel, telegramDestination, advanced, actionRow, message].every(Boolean)) return;

    const definitions = [
      { short: 'Source', title: '1 · Source', description: 'Choose where media comes from. The fields change between Local files and Telegram media.' },
      { short: 'Options & Checks', title: '2 · Options & Checks', description: 'Set the destination and verification rules before starting the transfer.' },
      { short: 'Review', title: '3 · Review', description: 'Confirm the selected source, destination, and delivery rules.' },
      { short: 'Run', title: '4 · Run', description: 'Start the job. Live Lane gives a quick glance; Jobs shows detailed progress and logs.' }
    ];
    const prefix = 'ax-transfer';
    const header = make('div', 'ax-manager-header');
    const titleGroup = make('div', 'ax-manager-title-group');
    const titleLine = make('div', 'ax-manager-title-line');
    if (mi) titleLine.append(mi);
    titleLine.append(title);
    titleGroup.append(titleLine);
    if (description) titleGroup.append(description);
    if (help) titleGroup.append(help);
    header.classList.add('ax-manager-header-statusless');
    header.append(titleGroup);

    const wizard = createStepPanels(prefix, definitions);
    const stepper = createStepBar(prefix, definitions.map(x => x.short), step => activateStep(card, prefix, step, definitions));
    const panelBodies = wizard.panels.map(x => x.body);

    addSectionLabel(panelBodies[0], 'SOURCE', 'Local folder or Telegram channel list');
    if (sourceRow) panelBodies[0].append(sourceRow);
    const sourceGrid = make('div', 'ax-manager-source-grid');
    const localSourceCard = make('div', 'ax-manager-source-choice ax-manager-mode-local');
    localSourceCard.append(localFolder);
    const telegramSourceCard = make('div', 'ax-manager-source-choice ax-manager-mode-telegram');
    if (telegramSourceLabel) telegramSourceCard.append(telegramSourceLabel);
    telegramSourceCard.append(telegramChannelPanel);
    const sourceHint = make('div', 'ax-manager-side-note ax-manager-mode-hint');
    sourceHint.append(make('strong', '', 'Preparation-first workflow'), make('p', '', 'Media is inventoried first. Existing metadata and playback assets are reused; new or incomplete records are prepared and saved before delivery.'));
    sourceGrid.append(localSourceCard, telegramSourceCard, sourceHint);
    panelBodies[0].append(sourceGrid);

    addSectionLabel(panelBodies[1], 'DESTINATION & DELIVERY RULES', 'The applicable options follow your source type');
    const optionsGrid = make('div', 'ax-manager-options-grid');
    const localOptions = make('div', 'ax-manager-source-choice ax-manager-mode-local');
    localOptions.id = 'ax-transfer-local-options';
    localDestination.id = 'ax-transfer-local-destination-row';
    if (localMediaModeToggle) localOptions.append(localMediaModeToggle);
    localOptions.append(localDestination);
    const telegramOptions = make('div', 'ax-manager-source-choice ax-manager-mode-telegram');
    telegramOptions.append(telegramDestination, advanced);
    const optionsNote = make('div', 'ax-manager-side-note');
    optionsNote.append(make('strong', '', 'Verification before delivery'), make('p', '', 'Select duplicate checks for Telegram transfers. Local source files are deleted only after verified delivery when that option is enabled. Cleanup of broken or orphaned items is optional.'));
    optionsGrid.append(localOptions, telegramOptions, optionsNote);
    panelBodies[1].append(optionsGrid);

    const review = make('div', 'ax-manager-review-wrap');
    review.id = 'ax-transfer-review-content';
    panelBodies[2].append(review);
    const runNote = make('div', 'ax-manager-run-note', 'The job runs in the background. Use Live Lane for a quick glance and Jobs for detailed progress and logs.');
    panelBodies[3].append(runNote, actionRow);

    definitions.forEach((item, index) => addNavigation(panelBodies[index], prefix, index + 1, definitions.length, definitions, step => activateStep(card, prefix, step, definitions)));

    const legacyShells = make('div', 'ax-manager-legacy-shells');
    if (localShell) legacyShells.append(localShell);
    if (telegramShell) legacyShells.append(telegramShell);
    const feedbackSink = make('div', 'ax-manager-status-sink');
    feedbackSink.hidden = true;
    feedbackSink.setAttribute('aria-hidden', 'true');
    if (message) feedbackSink.append(message);
    card.replaceChildren(header, stepper, wizard.progress, wizard.content, legacyShells, feedbackSink);
    card.classList.add('ax-manager-wizard', 'ax-transfer-wizard');
    card.dataset.axManagerWizard = 'ready';

    $('#u-transfer-source')?.addEventListener('change', () => {
      transferMode(card);
      activateStep(card, prefix, Number(card.dataset.axManagerCurrentStep || 1), definitions);
    });
    card.addEventListener('input', () => renderTransferReview(card));
    card.addEventListener('change', () => renderTransferReview(card));
    transferMode(card);
    activateStep(card, prefix, 1, definitions);
  }

  function buildMetadataWizard() {
    const card = $('#ax-transfer-metadata-slot .mc2');
    if (!card || card.dataset.axManagerWizard === 'ready') return;

    const mi = $('.mi', card), title = $('.mn', card);
    const description = $('.md', card), guidance = $('.ax-assets-guidance', card);
    const channelPanel = $('.ax-assets-channel-panel', card);
    const processingPanel = $('.ax-assets-processing-panel', card);
    if (![title, channelPanel, processingPanel].every(Boolean)) return;

    const scope = $('.ax-assets-scope-field', processingPanel);
    const runButton = $('#u-assets-run-selected', processingPanel);
    const runActions = $('.ax-assets-run-actions', processingPanel);
    const processPendingButton = $('#u-assets-process-pending', processingPanel);
    const advanced = $('.ax-assets-advanced-options', processingPanel);
    const moreActions = $('.ax-assets-more-actions', processingPanel);
    const legacyScope = $('.ax-assets-legacy-scope-controls', processingPanel);
    const message = $('#u-assets-process-msg', processingPanel);
    if (![scope, runButton, runActions, processPendingButton, advanced, legacyScope, message].every(Boolean)) return;

    const definitions = [
      { short: 'Channels', title: '1 · Channels', description: 'Choose the Telegram channels that should be inventoried and refreshed.' },
      { short: 'Processing Stages', title: '2 · Processing Stages', description: 'Choose the stages to run and any optional recheck rules.' },
      { short: 'Review', title: '3 · Review', description: 'Verify the selected channels, scope, and processing stages.' },
      { short: 'Run', title: '4 · Run', description: 'Choose the scan scope, then start the job. Live Lane gives a quick glance; Jobs shows detailed progress and logs.' }
    ];
    const prefix = 'ax-metadata';
    const header = make('div', 'ax-manager-header');
    const titleGroup = make('div', 'ax-manager-title-group');
    const titleLine = make('div', 'ax-manager-title-line');
    if (mi) titleLine.append(mi);
    titleLine.append(title);
    titleGroup.append(titleLine);
    if (description) titleGroup.append(description);
    if (guidance) titleGroup.append(guidance);
    header.classList.add('ax-manager-header-statusless');
    header.append(titleGroup);

    const wizard = createStepPanels(prefix, definitions);
    const stepper = createStepBar(prefix, definitions.map(x => x.short), step => activateStep(card, prefix, step, definitions));
    const panelBodies = wizard.panels.map(x => x.body);

    const channelSection = make('div', 'ax-manager-channel-section');
    channelSection.append(channelPanel);
    panelBodies[0].append(channelSection, make('p', 'ax-manager-inline-note', 'Select one or more channels. Use Search to narrow the list, or Select all for a full refresh.'));

    const advancedWrap = make('div', 'ax-manager-advanced-wrap');
    advancedWrap.append(advanced);
    if (moreActions) advancedWrap.append(moreActions); // optional: scene-match review now lives in Operations
    // Keep the legacy canonical scope controls attached for existing handlers, but hidden from users.
    if (legacyScope) advancedWrap.append(legacyScope);
    panelBodies[1].append(advancedWrap);

    const review = make('div', 'ax-manager-review-wrap');
    review.id = 'ax-metadata-review-content';
    panelBodies[2].append(review);
    const runNote = make('div', 'ax-manager-run-note', 'Archive X reuses valid saved results and runs the selected stages in dependency order. ');
    const runScope = make('div', 'ax-manager-run-scope');
    const runScopeHint = make('p', 'ax-manager-run-scope-note', 'Choose how many videos to process in each selected channel before starting.');
    if (scope) runScope.append(scope);
    runScope.append(runScopeHint);
    panelBodies[3].append(runNote, runScope, runActions);

    definitions.forEach((item, index) => addNavigation(panelBodies[index], prefix, index + 1, definitions.length, definitions, step => activateStep(card, prefix, step, definitions)));
    const feedbackSink = make('div', 'ax-manager-status-sink');
    feedbackSink.hidden = true;
    feedbackSink.setAttribute('aria-hidden', 'true');
    if (message) feedbackSink.append(message);
    card.replaceChildren(header, stepper, wizard.progress, wizard.content, feedbackSink);
    card.classList.add('ax-manager-wizard', 'ax-metadata-wizard');
    card.dataset.axManagerWizard = 'ready';

    card.addEventListener('input', () => renderMetadataReview(card));
    card.addEventListener('change', () => renderMetadataReview(card));
    document.addEventListener('click', event => {
      if (event.target.closest('#u-assets-run-selected, #u-assets-process-pending')) {
        window.setTimeout(() => renderMetadataReview(card), 350);
      }
    });
    activateStep(card, prefix, 1, definitions);
  }

  function init() {
    try { buildTransferWizard(); } catch (error) { console.error('Archive X transfer wizard UI setup failed:', error); }
    try { buildMetadataWizard(); } catch (error) { console.error('Archive X metadata wizard UI setup failed:', error); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
