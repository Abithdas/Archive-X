/* ============================================================================
 * FILE: js/151-ax-settings-parity.js
 * AREA: Settings
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* v29.4.332 — Settings parity with the UI reference.
   Presentation only: retitles section headings, adds the reference status rows / account card / count badges.
   No setting, handler, id or API is changed; the original nodes stay in place and keep updating. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const root = () => $('vs');
  const icon = (name) => `<svg class="ax-icon" aria-hidden="true"><use href="#ax-${name}"></use></svg>`;
  const text = (el) => String(el && el.textContent || '').replace(/\s+/g, ' ').trim();
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function setHead(card, title, right) {
    if (!card) return;
    const h = card.querySelector(':scope > .settings-v54-section-head');
    if (h) {
      const h3 = h.querySelector('h3'); if (h3) h3.textContent = title;
      const span = h.querySelector('span');
      if (span) { if (right == null) span.hidden = true; else { span.hidden = false; span.textContent = right; } }
      return;
    }
    const pnt = card.querySelector(':scope > .pnt, :scope > .ax-tg-connect-title');
    if (pnt) { pnt.textContent = title; }
  }
  function badge(host, id, cls, label) {
    if (!host) return null;
    let b = host.querySelector(`:scope > [data-axs-badge="${id}"]`);
    if (!b) { b = document.createElement('span'); b.dataset.axsBadge = id; b.style.marginLeft = 'auto'; host.appendChild(b); }
    b.className = 'pill ' + (cls || '');
    b.textContent = label;
    return b;
  }
  function count(card, label) {
    if (!card) return;
    const head = card.querySelector(':scope > .settings-v54-section-head, :scope > .pnt, :scope > .ax2-settings-status-title');
    if (!head) return;
    let c = head.querySelector('[data-axs-count]');
    if (!c) { c = document.createElement('span'); c.dataset.axsCount = '1'; c.className = 'ax-sp-count'; head.appendChild(c); }
    c.textContent = label;
  }

  /* ---------- Overview ---------- */
  function overview() {
    const card = root() && root().querySelector('.ax2-settings-status-card');
    if (card) count(card, '01 / 08');
    const set = (id, label) => { const e = $(id); if (!e) return; const cls = e.classList.contains('v54-ok') ? 'ok' : 'warn'; e.innerHTML = `<i class="ax-sp-dot"></i><span>${label}</span>`; e.dataset.axsTone = cls; };
    const tgOk = ($('settings-pill-telegram') || {}).classList && $('settings-pill-telegram').classList.contains('v54-ok');
    const metaOk = $('settings-pill-metadata') && $('settings-pill-metadata').classList.contains('v54-ok');
    const webOk = $('settings-pill-web') && $('settings-pill-web').classList.contains('v54-ok');
    set('settings-pill-telegram', tgOk ? 'Telegram · Connected' : 'Telegram · Not connected');
    set('settings-pill-metadata', metaOk ? 'Metadata · Providers linked' : 'Metadata · No providers');
    set('settings-pill-web', webOk ? 'Web login · Custom password' : 'Web login: default password');
    const cap = $('settings-config-caption');
    if (cap) { cap.textContent = 'Configuration changes are applied through the settings below; live jobs remain in Operations.'; if (card && cap.parentElement !== card) card.appendChild(cap); }
    statusList();
  }
  function statusList() {
    const card = $('settings-v54-status');
    if (!card) return;
    let list = card.querySelector('.ax-status-list');
    if (!list) {
      list = document.createElement('div'); list.className = 'list ax-status-list';
      card.appendChild(list);
      const more = document.createElement('details'); more.className = 'ax-status-more';
      more.innerHTML = `<summary>${icon('chevron-right')}Details · raw runtime report</summary><div class="ax-status-raw"></div>`;
      card.appendChild(more);
    }
    const summaryOf = (id) => { const s = $(id) && $(id).querySelector('.ax2-diagnostic-summary'); return s ? text(s) : text($(id)); };
    const tg = text($('set-tg-status')), tgOk = /connected/i.test(tg) && !/not connected|unavailable/i.test(tg);
    const deps = summaryOf('set-deps'), depsOk = /all found/i.test(deps);
    const ic = summaryOf('set-icons'), icOk = /icons ok$/i.test(ic) || /^icons ok/i.test(ic);
    const dbText = text($('v55-storage-size')), webOk = $('settings-pill-web') && $('settings-pill-web').classList.contains('v54-ok');
    const rows = [
      ['brand-telegram', 'Telegram', tgOk ? 'Connected · session saved' : (tg.replace(/^Telegram /i, '') || 'Checking…'), tgOk ? 'ok' : 'warn', tgOk ? 'OK' : 'Check', 'telegram'],
      ['gauge', 'Dependencies', deps || 'Checking…', depsOk ? 'ok' : (deps && !/checking/i.test(deps) ? 'warn' : ''), depsOk ? 'OK' : (deps && !/checking/i.test(deps) ? 'Check' : '…'), null],
      ['images', 'UI assets', ic || 'Checking…', icOk ? 'ok' : (ic && !/checking/i.test(ic) ? 'warn' : ''), icOk ? 'OK' : (ic && !/checking/i.test(ic) ? 'Check' : '…'), null],
      ['database', 'Database', dbText ? dbText.replace(/^Archive X data:\s*/i, 'Archive X data · ') : 'Local SQLite database', 'ok', 'OK', null],
      ['lock', 'Web login', webOk ? 'Custom login is set' : 'Still using the default password', webOk ? 'ok' : 'warn', webOk ? 'OK' : 'Change', 'security']
    ];
    const sig = JSON.stringify(rows);
    if (list.dataset.sig !== sig) {
      list.dataset.sig = sig;
      list.innerHTML = rows.map(([ic2, t, s, c, l, go]) => `<div${go ? ` data-axs-go="${go}"` : ''}><span class="ic">${icon(ic2)}</span><div class="tx"><b>${esc(t)}</b><small>${esc(s)}</small></div><span class="pill ${c}">${esc(l)}</span></div>`).join('');
    }
    const raw = card.querySelector('.ax-status-raw');
    if (raw) {
      const pres = [...card.querySelectorAll('.settings-status-grid pre')].map((p) => p.textContent).join('\n\n');
      raw.innerHTML = pres ? `<pre>${esc(pres)}</pre>` : '<div class="muted">No raw report yet. Use Refresh status.</div>';
    }
    const h = card.querySelector('.settings-v54-section-head span'); if (h) h.textContent = 'Runtime checks';
  }

  /* ---------- Telegram ---------- */
  let accountLoaded = false;
  async function telegram() {
    const panel = $('settings-v54-telegram'); if (!panel) return;
    const cred = $('ax-tg-credentials-panel');
    if (cred) {
      const t = cred.querySelector('.ax-tg-connect-title'); if (t) { t.textContent = 'API credentials'; badge(t, 'api', /not verified|unverified/i.test(text($('tg-api-status'))) ? '' : 'ok', /not verified|unverified/i.test(text($('tg-api-status'))) ? 'Not verified' : 'Valid'); }
    }
    const auth = $('login-pan');
    if (auth) { const h = auth.querySelector('.ax-tg-auth-head h3, .ax-tg-auth-head h4, .ax-tg-auth-head div > *:first-child'); if (h && !/Sign in/i.test(text(h))) h.textContent = 'Sign in to Telegram'; }
    if (!$('ax-account-card') && panel.firstElementChild) {
      const card = document.createElement('div'); card.id = 'ax-account-card'; card.className = 'pan ax-account-card';
      card.innerHTML = `<div class="ax-tg-connect-title">Account</div><div class="ax-account-row"><span class="av ax-account-av" data-ax-account-avatar><span data-ax-account-initial>A</span></span><div class="ax-account-id"><b data-ax-account-name>Telegram account</b><div class="mut" id="ax-account-sub">Checking…</div></div><button type="button" class="btn sm ax-account-edit" onclick="openAccountProfileEdit()"><svg class="ax-icon" aria-hidden="true"><use href="#ax-edit"></use></svg>Edit</button><span class="pill" id="ax-account-pill">Checking…</span></div>`;
      window.axAccountProfile && window.axAccountProfile.apply();
      panel.insertBefore(card, panel.firstElementChild.classList && panel.firstElementChild.classList.contains('settings-v54-section-head') ? panel.children[1] : panel.firstElementChild);
    }
    if (!accountLoaded && window.api) {
      accountLoaded = true;
      try {
        const r = await window.api('/api/settings/telegram', { cache: 'no-store' });
        const ok = !!(r && r.connected), pill = $('ax-account-pill'), sub = $('ax-account-sub');
        const phone = String((r && r.phone) || '').replace(/\D/g, '');
        const masked = phone.length > 4 ? '+' + phone.slice(0, Math.max(0, phone.length - 7)) + ' ••••• ••' + phone.slice(-3) : '';
        if (sub) sub.textContent = ok ? (masked || 'Signed in') : (r && r.configured ? 'Not signed in · API credentials saved' : 'Not signed in · add API credentials');
        if (pill) { pill.className = 'pill ' + (ok ? 'ok' : 'warn'); pill.innerHTML = ok ? '<i class="ax-sp-dot"></i>Connected' : 'Not connected'; }
        const t = $('ax-tg-credentials-panel') && $('ax-tg-credentials-panel').querySelector('.ax-tg-connect-title');
        if (t && r) badge(t, 'api', r.api_verified ? 'ok' : '', r.api_verified ? 'Valid' : 'Not verified');
      } catch (_) {}
    }
  }

  /* ---------- Metadata ---------- */
  function metadata() {
    const pans = [...document.querySelectorAll('#settings-v54-metadata > .pan')];
    if (!pans.length) return;
    const names = ['Provider authentication', 'Scan window', 'Provider status'];
    pans.forEach((p, i) => { const t = p.querySelector(':scope > .pnt'); if (t && names[i]) t.textContent = names[i]; });
    const tokens = [...document.querySelectorAll('#settings-v54-metadata .fr input[type=password], #settings-v54-metadata .fr input[placeholder*="saved"]')];
    const first = pans[0] && pans[0].querySelector(':scope > .pnt');
    if (first) { const n = tokens.filter((i) => /saved/i.test(i.placeholder || '')).length; badge(first, 'prov', n ? 'ok' : '', n ? n + ' linked' : 'Not linked'); }
    const scan = pans[1] && pans[1].querySelector(':scope > .pnt');
    if (scan) { const ins = [...pans[1].querySelectorAll('input[type=time]')]; if (ins.length > 1 && ins[0].value) badge(scan, 'win', 'acc', `${ins[0].value} – ${ins[1].value}`); }
    pans.forEach((p) => p.querySelectorAll('.fr > label').forEach((l) => {
      const small = l.querySelector('small'); if (small) return;
      const m = /^(.*?)\s*\((saved in[^)]*)\)\s*$/i.exec(text(l));
      if (m) { l.dataset.axsLabel = '1'; l.textContent = m[1]; }
    }));
  }

  /* ---------- Experience ---------- */
  function experience() {
    const card = $('settings-v55-experience'); if (!card) return;
    setHead(card, 'Playback', '04 / 08');
    const subs = { 'v55-autoplay': 'Start videos as soon as they open', 'v55-autonext': 'Continue with the next video when one ends', 'v55-resume-prompt': 'Offer to continue where you left off', 'v55-live-seek-preview': 'Show frames while scrubbing' };
    Object.keys(subs).forEach((id) => {
      const inp = $(id); const lab = inp && inp.closest('label'); if (!lab || lab.dataset.axsSub) return;
      lab.dataset.axsSub = '1';
      const span = lab.querySelector('span'); if (!span) return;
      span.innerHTML = `${esc(text(span))}<small class="mut">${esc(subs[id])}</small>`;
    });
  }

  /* ---------- Storage / Backup ---------- */
  function storage() {
    const st = $('settings-v55-storage'); if (st) setHead(st, 'Storage', '06 / 08');
    const bk = $('settings-v55-backup'); if (bk) setHead(bk, 'Backup & Restore', null);
    const cur = $('v55-storage-current'), size = $('v55-storage-size'), disk = $('v55-storage-disk');
    if (cur && size && disk && !st.querySelector('.ax-kv')) {
      const kv = document.createElement('div'); kv.className = 'ax-kv';
      [['Current data folder', cur], ['Archive X data', size], ['Drive', disk]].forEach(([l, el]) => {
        const d = document.createElement('div'); const s = document.createElement('span'); s.textContent = l; d.appendChild(s); d.appendChild(el); kv.appendChild(d);
      });
      const first = st.querySelector('.v55-data-card'); if (first) { first.textContent = ''; first.classList.add('ax-kv-host'); first.appendChild(kv); }
      const strip = (el, re) => { const f = () => { const t = el.textContent; const n = t.replace(re, ''); if (n !== t) el.textContent = n; }; f(); new MutationObserver(f).observe(el, { childList: true, characterData: true, subtree: true }); };
      strip(size, /^\s*Archive X data:\s*/i); strip(disk, /^\s*Drive:\s*/i);
    }
    const hist = document.getElementById('backup-history'); const hc = hist && hist.closest('.v55-data-card');
    if (hc) { const k = hc.querySelector(':scope > .k'); if (k) k.textContent = 'Backup history'; }
  }

  /* ---------- Security ---------- */
  function security() {
    const p = $('settings-v54-security'); if (!p) return;
    const pan = p.querySelector('.pan');
    const t = pan && pan.querySelector(':scope > .pnt'); if (t) t.textContent = 'Dashboard authentication';
    if (pan) count(pan, '07 / 08');
    const lab = (id, txt) => { const i = $(id); const l = i && i.closest('.fr') && i.closest('.fr').querySelector('label'); if (l) l.textContent = txt; };
    lab('web-pass-current', 'Current password'); lab('web-user', 'Username'); lab('web-pass', 'New password'); lab('web-pass2', 'Repeat new password');
    const i1 = $('web-pass'); if (i1) i1.placeholder = 'blank = keep'; const i2 = $('web-pass2'); if (i2) i2.placeholder = 'Repeat';
    const wd = $('wl-default'); if (wd) wd.classList.add('ax-note-warn');
  }

  /* ---------- System ---------- */
  function system() {
    const b = $('settings-browser-compat'); if (b) setHead(b, 'Browser compatibility', '08 / 08');
    const h = $('settings-hardware-adaptive'); if (h) setHead(h, 'System & network optimization', 'Hardware-aware');
    const result = $('internet-test-result');
    const status = $('internet-test-status');
    const actions = status && status.parentElement;
    // Keep the original controls and handlers, but normalize rebuilt/legacy panel order.
    if (h && result && actions && result.parentElement === h && actions.parentElement === h && h.lastElementChild !== actions) {
      h.appendChild(actions);
    }
  }

  let settingsObserver = null;
  const OBSERVE = { childList: true, subtree: true, characterData: true };
  function run() {
    // These passes rewrite labels/counters themselves. Observing our own writes re-queued run() every
    // 60ms forever (hidden or not), so pause the observer while we write and resume afterwards.
    if (settingsObserver) settingsObserver.disconnect();
    try { overview(); telegram(); metadata(); experience(); storage(); security(); system(); } catch (e) { /* presentation only */ }
    if (settingsObserver) { const vs = root(); if (vs) settingsObserver.observe(vs, OBSERVE); }
  }
  let t = null;
  function schedule() { clearTimeout(t); t = setTimeout(run, 60); }

  function init() {
    const vs = root(); if (!vs) return false;
    if (!vs.querySelector('.ax2-settings-panel')) return false;
    run();
    settingsObserver = new MutationObserver(schedule);
    settingsObserver.observe(vs, OBSERVE);
    vs.addEventListener('click', (e) => {
      const go = e.target.closest && e.target.closest('[data-axs-go]');
      if (go && window.axSettingsShowCategory) window.axSettingsShowCategory(go.dataset.axsGo);
    });
    return true;
  }
  let tries = 0;
  (function wait() { if (init() || ++tries > 80) return; setTimeout(wait, 150); })();
})();
