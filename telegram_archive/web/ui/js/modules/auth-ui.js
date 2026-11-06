/* ============================================================================
 * FILE: js/modules/auth-ui.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Owns the login gate, first-run setup, and session transitions. */
(function createAuthUi(global) {
  const getElement = id => global.document.getElementById(id);
  let loginHintRequest = null;
  let sessionGatePromise = null;
  let startApplication = null;

  function startAuthenticatedApp() {
    if (typeof startApplication !== 'function') {
      throw new Error('The app boot callback has not been registered.');
    }
    startApplication();
  }

  async function completeSetup() {
    getElement('lerr').textContent = '';
    const username = getElement('lu').value.trim() || 'admin';
    const password = getElement('lsetup-pw').value;
    const confirmation = getElement('lsetup-pw2').value;
    const response = await global.fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, confirmation }),
      credentials: 'include'
    }).catch(() => null);
    if (response?.ok) {
      getElement('lerr').style.color = 'var(--green)';
      getElement('lerr').textContent = 'Administrator created. Sign in with your new credentials.';
      getElement('setup-panel').style.display = 'none';
      getElement('login-submit').style.display = 'block';
      getElement('lp2').closest('.fr').style.display = 'grid';
      getElement('lp2').value = '';
      getElement('lsetup-pw').value = '';
      getElement('lsetup-pw2').value = '';
      getElement('lhint').style.display = 'none';
      const subtitle = getElement('lp').querySelector('.ax2-signin-subtitle');
      if (subtitle) subtitle.textContent = 'Sign in to continue';
      return;
    }
    const body = await response?.json().catch(() => ({}));
    getElement('lerr').style.color = 'var(--red)';
    getElement('lerr').textContent = body?.detail || 'Could not complete administrator setup.';
  }

  async function doLogin() {
    const submit = getElement('login-submit');
    if (submit.disabled) return;
    submit.disabled = true;
    getElement('lerr').style.color = 'var(--red)';
    getElement('lerr').textContent = '';
    try {
      const response = await global.fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: getElement('lu').value.trim(),
          password: getElement('lp2').value
        }),
        credentials: 'include'
      }).catch(() => null);
      if (response?.ok) {
        const session = await global.fetch('/api/status', {
          credentials: 'include',
          cache: 'no-store'
        }).catch(() => null);
        if (!session?.ok) {
          getElement('lerr').textContent = session?.status === 401
            ? 'Your browser did not keep the sign-in session. Open the preview in a new tab and sign in again.'
            : 'Could not verify your sign-in. Please try again.';
          return;
        }
        global.ArchiveXDiagnostics?.sessionRecovered?.();
        getElement('lp2').value = '';
        hideLP();
        startAuthenticatedApp();
        return;
      }
      let message = 'Wrong username or password.';
      if (!response) message = 'Cannot reach the app. Is it still running?';
      else if (response.status === 429) {
        const body = await response.json().catch(() => ({}));
        message = body.detail || 'Too many attempts. Wait a few minutes.';
      }
      getElement('lerr').textContent = message;
    } finally {
      submit.disabled = false;
    }
  }

  async function doLogout() {
    try {
      const response = await global.fetch('/logout', {
        method: 'POST',
        credentials: 'include',
        cache: 'no-store'
      });
      if (!response.ok) console.warn('Logout request returned', response.status);
    } catch (error) {
      console.warn('Logout request failed', error);
    }
    global.resetApiClientState?.();
    global.location.replace('/login');
  }

  function showLP() {
    global.stopOpsSocket?.();
    global.clearApiResponseCache?.();
    getElement('app').style.display = 'none';
    const loginPanel = getElement('lp');
    loginPanel.style.display = 'flex';
    if (loginHintRequest) return;

    loginHintRequest = global.fetch('/api/login-hint', { cache: 'no-store' })
      .then(response => response.json())
      .then(hint => {
        const setupRequired = !!hint.setup_required;
        getElement('setup-panel').style.display = setupRequired ? 'block' : 'none';
        getElement('login-submit').style.display = setupRequired ? 'none' : 'block';
        getElement('lp2').closest('.fr').style.display = setupRequired ? 'none' : 'grid';
        const subtitle = loginPanel.querySelector('.ax2-signin-subtitle');
        if (subtitle) subtitle.textContent = setupRequired ? 'Create your administrator account' : 'Sign in to continue';
        getElement('lhint').style.display = setupRequired ? 'block' : 'none';
        if (setupRequired) {
          getElement('lhint').textContent = 'First-run security setup is required. Create a new administrator password (12+ characters) before signing in.';
        }
      })
      .catch(() => { loginHintRequest = null; });
  }

  function hideLP() {
    loginHintRequest = null;
    getElement('app').style.display = 'flex';
    getElement('lp').style.display = 'none';
  }

  function initializeSessionGate(bootApp) {
    if (typeof bootApp !== 'function') {
      throw new TypeError('initializeSessionGate requires the app boot callback.');
    }
    if (sessionGatePromise) {
      if (startApplication !== bootApp) {
        throw new Error('The app boot callback is already registered.');
      }
      return sessionGatePromise;
    }

    startApplication = bootApp;
    sessionGatePromise = (async () => {
      const response = await global.fetch('/api/status', {
        credentials: 'include',
        cache: 'no-store'
      }).catch(() => null);
      if (!response || response.status === 401) {
        showLP();
        return;
      }
      hideLP();
      startAuthenticatedApp();
    })();
    return sessionGatePromise;
  }

  const authUi = Object.freeze({
    completeSetup,
    doLogin,
    doLogout,
    showLP,
    hideLP,
    initializeSessionGate
  });
  global.ArchiveXAuth = authUi;
  Object.assign(global, authUi);
})(window);
