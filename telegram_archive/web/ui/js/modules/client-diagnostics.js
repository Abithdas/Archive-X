/* ============================================================================
 * FILE: js/modules/client-diagnostics.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Browser diagnostics module.
 *
 * This classic-script module exports one explicit namespace so the legacy UI
 * can consume diagnostics without owning its queue, redaction, or transport.
 */
(function createClientDiagnostics(global) {
  const PLAYBACK_INCIDENT_COOLDOWN_MS = 5000;
  const PLAYBACK_INCIDENT_DELAY_MS = 750;
  const state = {
    items: [],
    max: 160,
    sending: false,
    sessionId: (global.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`),
    seenResources: new Set(),
    pendingResources: [],
    maxResources: 120,
    lastSnapshotAt: 0,
    failures: 0,
    nextAttemptAt: 0,
    authBlocked: false,
    longTasks: 0
  };

  function redactText(value) {
    return String(value ?? '')
      .replace(/\b(authorization|cookie|set-cookie|[\w-]*password|passwd|[\w-]*secret|[\w-]*token|api[_-]?key|api[_-]?hash|session[_-]?id)\b(\s*[:=]\s*|\s+)(["']?)[^,\s;&"']+/gi, '$1$2<REDACTED>')
      .replace(/\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+/gi, '$1 <REDACTED>')
      .replace(/([?&](?:access_token|refresh_token|auth|authorization|password|secret|token|api_key|api_hash)=)[^&#\s]+/gi, '$1<REDACTED>');
  }

  function safeValue(value, depth = 0) {
    if (depth > 5) return '[truncated]';
    if (value === null || typeof value === 'boolean' || typeof value === 'number') return value;
    if (typeof value === 'string') return redactText(value).slice(0, 3000);
    if (Array.isArray(value)) return value.slice(0, 80).map(item => safeValue(item, depth + 1));
    if (value && typeof value === 'object') {
      const safe = {};
      for (const [key, item] of Object.entries(value).slice(0, 80)) {
        const name = String(key).slice(0, 100);
        if (/body/i.test(name)) continue;
        safe[name] = /password|passwd|secret|token|authorization|cookie|credential|api[_-]?key|api[_-]?hash|^headers?$/i.test(name)
          ? '<REDACTED>'
          : safeValue(item, depth + 1);
      }
      return safe;
    }
    return redactText(value).slice(0, 3000);
  }

  function safeUrl(raw) {
    try {
      const url = new URL(String(raw || ''), global.location.href);
      return redactText(url.origin === global.location.origin ? url.pathname : url.origin).slice(0, 500);
    } catch {
      return redactText(String(raw || '').split('?')[0]).slice(0, 500);
    }
  }

  function mediaSnapshot() {
    const video = global.document?.getElementById('pv');
    if (!video) return null;
    const buffered = [];
    const seekable = [];
    try {
      for (let i = 0; i < video.buffered.length && i < 12; i++) {
        buffered.push([Math.round(video.buffered.start(i) * 1000) / 1000, Math.round(video.buffered.end(i) * 1000) / 1000]);
      }
    } catch {}
    try {
      for (let i = 0; i < video.seekable.length && i < 12; i++) {
        seekable.push([Math.round(video.seekable.start(i) * 1000) / 1000, Math.round(video.seekable.end(i) * 1000) / 1000]);
      }
    } catch {}
    const error = video.error
      ? { code: video.error.code || null, message: redactText(video.error.message || '').slice(0, 300) }
      : null;
    return {
      src: safeUrl(video.currentSrc || video.src || ''),
      current_time: Number.isFinite(video.currentTime) ? Math.round(video.currentTime * 1000) / 1000 : null,
      duration: Number.isFinite(video.duration) ? Math.round(video.duration * 1000) / 1000 : null,
      paused: !!video.paused,
      seeking: !!video.seeking,
      ended: !!video.ended,
      ready_state: video.readyState,
      network_state: video.networkState,
      playback_rate: video.playbackRate,
      volume: video.volume,
      muted: video.muted,
      video_width: video.videoWidth,
      video_height: video.videoHeight,
      buffered,
      seekable,
      error
    };
  }

  function discoverResources() {
    const output = [];
    try {
      const entries = global.performance.getEntriesByType('resource');
      for (let i = Math.max(0, entries.length - 80); i < entries.length; i++) {
        if (output.length >= 40) break;
        const entry = entries[i];
        const name = safeUrl(entry.name);
        const key = [name, entry.startTime, entry.duration, entry.transferSize].join('|');
        if (state.seenResources.has(key)) continue;
        state.seenResources.add(key);
        output.push({
          name,
          initiator: redactText(entry.initiatorType || '').slice(0, 30),
          start_ms: Math.round(entry.startTime),
          duration_ms: Math.round(entry.duration),
          transfer_size: entry.transferSize || 0,
          encoded_size: entry.encodedBodySize || 0,
          decoded_size: entry.decodedBodySize || 0,
          request_start_ms: Math.round(entry.requestStart || 0),
          response_start_ms: Math.round(entry.responseStart || 0),
          response_end_ms: Math.round(entry.responseEnd || 0)
        });
        if (state.seenResources.size > 600) {
          state.seenResources.delete(state.seenResources.values().next().value);
        }
      }
    } catch {}
    return output.slice(-40);
  }

  function browserSnapshot() {
    const connection = global.navigator.connection || global.navigator.mozConnection || global.navigator.webkitConnection;
    const memory = global.performance.memory
      ? {
          used_js_heap: global.performance.memory.usedJSHeapSize,
          total_js_heap: global.performance.memory.totalJSHeapSize,
          js_heap_limit: global.performance.memory.jsHeapSizeLimit
        }
      : null;
    return {
      session_id: state.sessionId,
      captured_at: new Date().toISOString(),
      url: safeUrl(global.location.href),
      page: redactText(global.location.pathname).slice(0, 500),
      visibility: global.document.visibilityState,
      online: global.navigator.onLine,
      hardware_concurrency: global.navigator.hardwareConcurrency || null,
      device_memory: global.navigator.deviceMemory || null,
      viewport: { width: global.innerWidth, height: global.innerHeight, dpr: global.devicePixelRatio || 1 },
      network: connection
        ? {
            effective_type: connection.effectiveType || null,
            rtt_ms: connection.rtt || null,
            downlink_mbps: connection.downlink || null,
            save_data: !!connection.saveData,
            type: connection.type || null
          }
        : null,
      memory,
      media: mediaSnapshot(),
      long_tasks: state.longTasks
    };
  }

  function record(type, data = {}, level = 'INFO') {
    try {
      const row = {
        type: redactText(type || 'client.event').slice(0, 80),
        level: redactText(level || 'INFO').toUpperCase(),
        message: redactText(data.message || '').slice(0, 1000),
        url: safeUrl(global.location.href),
        page: redactText(global.location.pathname).slice(0, 500),
        browser: redactText(global.navigator.userAgent).slice(0, 180),
        status: data.status ?? null,
        method: redactText(data.method || '').slice(0, 16),
        duration_ms: data.duration_ms ?? null,
        data: data.data && typeof data.data === 'object' ? safeValue(data.data) : {}
      };
      state.items.push(row);
      if (state.items.length > state.max) state.items.splice(0, state.items.length - state.max);
      if (state.items.length >= 12) void flush();
    } catch {}
  }

  async function flush(force = false) {
    if (state.sending || state.authBlocked || global.location.pathname === '/login' || !global.navigator.onLine || Date.now() < state.nextAttemptAt) return;
    const discovered = discoverResources();
    if (discovered.length) state.pendingResources.push(...discovered);
    if (state.pendingResources.length > state.maxResources) state.pendingResources.splice(state.maxResources);
    const batch = state.items.splice(0, 40);
    const resources = state.pendingResources.splice(0, 40);
    const sendSnapshot = !!force && Date.now() - state.lastSnapshotAt >= 15000;
    if (!batch.length && !resources.length && !sendSnapshot) return;
    const snapshot = browserSnapshot();
    state.sending = true;
    const controller = new global.AbortController();
    const timeout = global.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await global.fetch('/api/diagnostics/client', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        cache: 'no-store',
        body: JSON.stringify({ session_id: state.sessionId, events: batch, resources, snapshot })
      });
      if (!response.ok) {
        state.items = [...batch, ...state.items].slice(0, state.max);
        state.pendingResources = [...resources, ...state.pendingResources].slice(0, state.maxResources);
        if (response.status === 401 || response.status === 403) {
          state.authBlocked = true;
          return;
        }
        const retryAfter = Number(response.headers?.get?.('Retry-After'));
        state.failures++;
        state.nextAttemptAt = Date.now() + (
          Number.isFinite(retryAfter) && retryAfter > 0
            ? Math.min(300000, retryAfter * 1000)
            : Math.min(300000, 1000 * 2 ** Math.min(state.failures - 1, 8))
        );
        return;
      }
      state.failures = 0;
      state.nextAttemptAt = 0;
      state.lastSnapshotAt = Date.now();
    } catch {
      state.items = [...batch, ...state.items].slice(0, state.max);
      state.pendingResources = [...resources, ...state.pendingResources].slice(0, state.maxResources);
      state.failures++;
      state.nextAttemptAt = Date.now() + Math.min(300000, 1000 * 2 ** Math.min(state.failures - 1, 8));
    } finally {
      global.clearTimeout(timeout);
      state.sending = false;
    }
  }

  function sessionRecovered() {
    if (!state.authBlocked) return;
    state.authBlocked = false;
    state.failures = 0;
    state.nextAttemptAt = 0;
  }

  if (global.PerformanceObserver) {
    try {
      const observer = new global.PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (entry.duration >= 50) {
            state.longTasks++;
            record('browser.longtask', {
              message: 'Main-thread long task',
              data: { duration_ms: Math.round(entry.duration), start_ms: Math.round(entry.startTime) }
            }, 'WARN');
          }
        }
      });
      observer.observe({ type: 'longtask', buffered: true });
    } catch {}
  }
  if (global.navigator.connection?.addEventListener) {
    global.navigator.connection.addEventListener('change', () => record('browser.network_change', { data: { network: browserSnapshot().network } }));
  }
  global.addEventListener('online', () => {
    state.nextAttemptAt = 0;
    record('browser.online');
  });
  global.addEventListener('offline', () => record('browser.offline', { message: 'Browser reported offline' }, 'WARN'));
  global.addEventListener('error', event => {
    const target = event.target;
    if (target && target !== global) {
      const tag = String(target.tagName || '').toLowerCase();
      const src = String(target.currentSrc || target.src || target.href || '').slice(-500);
      record('resource.load_error', { message: `Resource load failed: ${tag || 'resource'}`, data: { tag, src } }, 'WARN');
      return;
    }
    record('js.exception', {
      message: event.message || 'Browser JavaScript exception',
      data: {
        file: String(event.filename || '').slice(-300),
        line: event.lineno || 0,
        column: event.colno || 0,
        stack: String(event.error?.stack || '').slice(-3000)
      }
    }, 'ERROR');
  }, true);
  global.addEventListener('unhandledrejection', event => record('js.unhandled_rejection', {
    message: event.reason?.message || String(event.reason || 'Unhandled rejection'),
    data: { stack: String(event.reason?.stack || '').slice(-3000) }
  }, 'ERROR'));
  global.addEventListener('pagehide', () => {
    try { void flush(); } catch {}
  });
  global.setInterval(() => { void flush(true); }, 5000);

  global.ArchiveXDiagnostics = Object.freeze({
    state,
    playbackIncidentCooldownMs: PLAYBACK_INCIDENT_COOLDOWN_MS,
    playbackIncidentDelayMs: PLAYBACK_INCIDENT_DELAY_MS,
    record,
    flush,
    sessionRecovered,
    media: mediaSnapshot
  });
})(window);
