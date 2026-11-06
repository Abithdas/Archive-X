/* ============================================================================
 * FILE: js/105-exit-app.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Explicit application shutdown. Use Archive X dialogs and confirm when the server is actually offline. */
(function () {
  'use strict';
  let pending = false;
  let monitorStartedAt = 0;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function setHeaderControls(disabled, label) {
    const button = document.getElementById('exit-app-btn');
    const signout = document.querySelector('#hdr .signout-btn');
    if (button) {
      button.disabled = !!disabled;
      button.textContent = label || 'Exit app';
    }
    if (signout) signout.disabled = !!disabled;
  }

  function ensureExitOverlay() {
    let overlay = document.getElementById('ax-exit-overlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'ax-exit-overlay';
    overlay.className = 'ax-exit-overlay';
    overlay.hidden = true;
    overlay.dataset.state = 'stopping';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = [
      '<div class="ax-exit-dialog" role="dialog" aria-modal="true" aria-labelledby="ax-exit-title">',
      '<span class="ax-exit-mark" aria-hidden="true">AX</span>',
      (window.AXLoadingFeedback?.rollerMarkup?.('lg', 'ax-exit-spinner') || '<span class="ax-lds-roller lg ax-exit-spinner" aria-hidden="true">' + '<i></i>'.repeat(8) + '</span>'),
      '<h2 class="ax-exit-title" id="ax-exit-title"></h2>',
      '<p class="ax-exit-message" id="ax-exit-message"></p>',
      '<div class="ax-exit-actions">',
      '<button type="button" class="btn pri" id="ax-exit-close-tab" hidden>Close this tab</button>',
      '<button type="button" class="btn danger" id="ax-exit-retry" hidden>Retry shutdown</button>',
      '<button type="button" class="btn" id="ax-exit-return" hidden>Return to Archive X</button>',
      '</div></div>'
    ].join('');
    document.body.appendChild(overlay);
    overlay.querySelector('#ax-exit-close-tab')?.addEventListener('click', () => {
      try { window.close(); } catch (_) {}
      setTimeout(() => {
        if (!window.closed) {
          const message = overlay.querySelector('#ax-exit-message');
          if (message) message.textContent = 'Archive X has stopped. This browser did not allow the tab to close automatically; you can close it manually now.';
        }
      }, 250);
    });
    overlay.querySelector('#ax-exit-retry')?.addEventListener('click', () => { void window.exitArchiveX(true); });
    overlay.querySelector('#ax-exit-return')?.addEventListener('click', () => {
      overlay.hidden = true;
      overlay.dataset.state = 'idle';
      pending = false;
      setHeaderControls(false, 'Exit app');
    });
    return overlay;
  }

  function showShutdownNotice(title, message, state) {
    const overlay = ensureExitOverlay();
    overlay.dataset.state = state || 'stopping';
    overlay.querySelector('#ax-exit-title').textContent = title;
    overlay.querySelector('#ax-exit-message').textContent = message;
    const mark = overlay.querySelector('.ax-exit-mark');
    mark.textContent = state === 'stopped' ? '✓' : (state === 'failed' || state === 'slow' ? '!' : 'AX');
    overlay.querySelector('#ax-exit-close-tab').hidden = state !== 'stopped';
    overlay.querySelector('#ax-exit-retry').hidden = !['failed', 'slow'].includes(state);
    overlay.querySelector('#ax-exit-return').hidden = !['failed', 'slow'].includes(state);
    overlay.hidden = false;
    if (state === 'stopped') overlay.querySelector('#ax-exit-close-tab')?.focus();
    else if (state === 'failed' || state === 'slow') overlay.querySelector('#ax-exit-retry')?.focus();
  }

  async function waitForServerStop() {
    monitorStartedAt = Date.now();
    let consecutiveOffline = 0;
    let lastStillLiveNotice = 0;
    while (Date.now() - monitorStartedAt < 30000) {
      await sleep(750);
      let serverResponded = false;
      let controller;
      let timeout;
      try {
        controller = new AbortController();
        timeout = setTimeout(() => controller.abort(), 1800);
        // A tiny endpoint avoids repeatedly serializing the full Operations status payload.
        await fetch('/api/app/ping', {
          method: 'GET', credentials: 'same-origin', cache: 'no-store',
          headers: { 'Cache-Control': 'no-cache' }, signal: controller.signal
        });
        serverResponded = true;
      } catch (_) {
        serverResponded = false;
      } finally {
        if (timeout) clearTimeout(timeout);
      }
      if (serverResponded) {
        consecutiveOffline = 0;
        const elapsed = Date.now() - monitorStartedAt;
        if (elapsed > 8000 && elapsed - lastStillLiveNotice > 5000) {
          lastStillLiveNotice = elapsed;
          showShutdownNotice(
            'Archive X is still closing',
            'The server is still responding while background services finish shutting down. Keep this page open; Archive X will confirm when the server stops.',
            'stopping'
          );
        }
      } else {
        consecutiveOffline += 1;
        if (consecutiveOffline >= 3) {
          showShutdownNotice(
            'Archive X has stopped',
            'The Archive X server is no longer responding. Shutdown is complete. You can close this tab, then launch Archive X again whenever you need it.',
            'stopped'
          );
          try { window.ArchiveXDiagnostics?.record('app.exit_completed', { message: 'Archive X server stopped responding after graceful exit' }); } catch (_) {}
          return true;
        }
      }
    }
    pending = false;
    setHeaderControls(false, 'Exit app');
    showShutdownNotice(
      'Archive X is still running',
      'The server did not stop within 30 seconds and is still responding. You can retry the shutdown or return to Archive X and inspect Operations for a long-running task.',
      'slow'
    );
    return false;
  }

  window.exitArchiveX = async function exitArchiveX(skipConfirmation) {
    if (pending) return;
    let accepted = !!skipConfirmation;
    if (!accepted) {
      const confirmInApp = window.axConfirm;
      if (typeof confirmInApp !== 'function') {
        showShutdownNotice('Cannot open Archive X confirmation', 'The in-app confirmation dialog has not loaded. No shutdown was requested. Reload Archive X and try again.', 'failed');
        return;
      }
      accepted = await confirmInApp(
        'Exit Archive X completely? This stops the local server and background workers. Running jobs will be saved for recovery where supported and may need to be resumed when you launch Archive X again. This is different from Sign out. Continue?',
        { title: 'Exit Archive X?', okLabel: 'Exit app', cancelLabel: 'Keep Archive X open', danger: true }
      );
    }
    if (!accepted) return;

    pending = true;
    setHeaderControls(true, 'Exiting…');
    showShutdownNotice(
      'Preparing to close Archive X',
      'Sending the graceful shutdown request. Archive X will save recovery state where supported and stop its background services.',
      'stopping'
    );

    try {
      const response = await fetch('/api/app/exit', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' }, body: '{}'
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok !== true) {
        throw new Error(payload.detail || `Shutdown request failed (HTTP ${response.status}).`);
      }
      try { window.ArchiveXDiagnostics?.record('app.exit_requested', { message: 'User requested graceful Archive X shutdown' }); } catch (_) {}
      showShutdownNotice(
        'Archive X is shutting down',
        'The request was accepted. Waiting for the server and background workers to stop…',
        'stopping'
      );
      await waitForServerStop();
    } catch (error) {
      pending = false;
      setHeaderControls(false, 'Exit app');
      showShutdownNotice(
        'Could not stop Archive X',
        `${error?.message || error} No successful shutdown was confirmed. You can retry or return to Archive X.`,
        'failed'
      );
    }
  };
})();
