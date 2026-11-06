/* ============================================================================
 * FILE: js/player/preview-frame-cache.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X preview-frame cache module.
 * Shared by the 36-frame pre-play grid and recommendation-card slideshows.
 * Intentionally has no dependency on the playback engine or player DOM.
 */
(function registerArchiveXPreviewFrameCache(global) {
  'use strict';

  function createPreviewFrameCache(options = {}) {
    const keyFor = typeof options.keyFor === 'function'
      ? options.keyFor
      : ((cid, mid) => String(cid) + ':' + String(mid));
    const manifestCache = new Map();
    const frameImageCache = new Map();
    const frameWaiters = new Map();
    const gridSpriteCache = new Map();
    const MANIFEST_READY_TTL_MS = 120000;
    const MANIFEST_MISS_TTL_MS = 6000;
    const FRAME_CACHE_MAX = 140;

    async function getManifest(cid, mid, force = false) {
      const key = keyFor(cid, mid), now = Date.now(), hit = manifestCache.get(key);
      if (!force && hit && hit.promise) return hit.promise;
      if (!force && hit && hit.expiresAt > now) return hit.info;
      const promise = (async () => {
        try {
          const response = await fetch(
            `/api/play/preview-manifest/${encodeURIComponent(cid)}/${encodeURIComponent(mid)}`,
            { credentials: 'include', cache: 'no-store' }
          );
          if (!response.ok) throw new Error(`preview manifest ${response.status}`);
          const info = await response.json();
          const valid = !!info && info.frames_ready === true && Number(info.frame_count || 0) >= 36;
          const revision = String(info?.updated_at || info?.version || '');
          const normalized = { ...info, frames_ready: valid, revision };
          manifestCache.set(key, {
            info: normalized,
            expiresAt: Date.now() + (valid ? MANIFEST_READY_TTL_MS : MANIFEST_MISS_TTL_MS)
          });
          return normalized;
        } catch (error) {
          const info = { frames_ready: false, frame_count: 0, revision: '', error: String(error?.message || error) };
          manifestCache.set(key, { info, expiresAt: Date.now() + MANIFEST_MISS_TTL_MS });
          return info;
        }
      })();
      manifestCache.set(key, { info: hit?.info || null, promise, expiresAt: now + MANIFEST_MISS_TTL_MS });
      return promise;
    }

    async function loadGridSprite(cid, mid, manifest = {}) {
      const key = `${keyFor(cid, mid)}:${String(manifest.revision || manifest.updated_at || manifest.version || '')}`;
      const hit = gridSpriteCache.get(key);
      if (hit) { gridSpriteCache.delete(key); gridSpriteCache.set(key, hit); return hit; }
      const promise = (async () => {
        let objectUrl = '';
        try {
          const response = await fetch(
            `/api/play/preview-grid/${encodeURIComponent(cid)}/${encodeURIComponent(mid)}`,
            { credentials: 'include', cache: 'default' }
          );
          if (!response.ok) throw new Error(`preview grid ${response.status}`);
          const blob = await response.blob();
          if (!blob.size) throw new Error('empty preview grid');
          objectUrl = URL.createObjectURL(blob);
          const image = new Image(); image.decoding = 'async';
          if (typeof image.decode === 'function') {
            image.src = objectUrl;
            await image.decode();
          } else {
            await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = objectUrl; });
          }
          image.__axObjectURL = objectUrl; image.__axGridSprite = true;
          return image;
        } catch (_) {
          try { if (objectUrl) URL.revokeObjectURL(objectUrl); } catch (_) {}
          return null;
        }
      })();
      gridSpriteCache.set(key, promise);
      const image = await promise;
      if (!image) { gridSpriteCache.delete(key); }
      else {
        // LRU promotion: move to end so eviction removes least-recently-used
        gridSpriteCache.delete(key);
        gridSpriteCache.set(key, Promise.resolve(image));
      }
      while (gridSpriteCache.size > 12) {
        const [oldKey, oldPromise] = gridSpriteCache.entries().next().value;
        if (oldKey === key) break;
        gridSpriteCache.delete(oldKey);
        Promise.resolve(oldPromise).then(oldImage => { try { URL.revokeObjectURL(oldImage?.__axObjectURL || ''); } catch (_) {} });
      }
      return image;
    }

    function rememberFrameInCallerCache(cache, index, image, maxFrames) {
      if (!cache || !image) return;
      cache.set(index, image);
      while (cache.size > Math.max(1, Number(maxFrames) || 8)) {
        const first = cache.keys().next().value;
        if (first === index) break;
        cache.delete(first); // Shared pool owns and revokes the object URL.
      }
    }

    async function loadFrame(cid, mid, index, cache, wait, maxFrames = 8, revision = '') {
      const idx = Math.max(0, Math.min(35, Number(index) || 0));
      if (cache?.has(idx)) return cache.get(idx);
      const rev = String(revision || '');
      const sharedKey = `${keyFor(cid, mid)}:${rev}:${idx}`;
      const pooled = frameImageCache.get(sharedKey);
      if (pooled) {
        frameImageCache.delete(sharedKey);
        frameImageCache.set(sharedKey, pooled);
        rememberFrameInCallerCache(cache, idx, pooled, maxFrames);
        return pooled;
      }
      if (frameWaiters.has(sharedKey)) {
        const image = await frameWaiters.get(sharedKey);
        if (image) rememberFrameInCallerCache(cache, idx, image, maxFrames);
        return image;
      }
      if (wait?.has(idx)) {
        const image = await wait.get(idx);
        if (image) rememberFrameInCallerCache(cache, idx, image, maxFrames);
        return image;
      }
      const query = rev ? `?rev=${encodeURIComponent(rev)}` : '';
      const url = `/api/play/preview-frame/${encodeURIComponent(cid)}/${encodeURIComponent(mid)}/${idx}${query}`;
      const promise = (async () => {
        let objectUrl = '';
        try {
          const response = await fetch(url, { credentials: 'include', cache: 'default' });
          if (!response.ok) throw new Error(`preview frame ${response.status}`);
          const blob = await response.blob();
          if (!blob.size) throw new Error('empty preview frame');
          objectUrl = URL.createObjectURL(blob);
          const image = new Image();
          image.decoding = 'async';
          await new Promise((resolve, reject) => {
            image.onload = resolve;
            image.onerror = reject;
            image.src = objectUrl;
          });
          image.__axObjectURL = objectUrl;
          image.__axSharedFrameKey = sharedKey;
          frameImageCache.set(sharedKey, image);
          while (frameImageCache.size > FRAME_CACHE_MAX) {
            const [oldKey, oldImage] = frameImageCache.entries().next().value;
            frameImageCache.delete(oldKey);
            try { URL.revokeObjectURL(oldImage?.__axObjectURL || ''); } catch (_) { /* best-effort cleanup */ }
          }
          return image;
        } catch (_) {
          try { if (objectUrl) URL.revokeObjectURL(objectUrl); } catch (_) { /* best-effort cleanup */ }
          return null;
        } finally {
          frameWaiters.delete(sharedKey);
          if (wait) wait.delete(idx);
        }
      })();
      frameWaiters.set(sharedKey, promise);
      if (wait) wait.set(idx, promise);
      const image = await promise;
      if (image) rememberFrameInCallerCache(cache, idx, image, maxFrames);
      return image;
    }

    return Object.freeze({
      getManifest,
      loadFrame,
      loadGridSprite,
      rememberFrameInCallerCache,
      // Diagnostic-only state; no caller may mutate the maps.
      getStats: () => ({ manifests: manifestCache.size, frames: frameImageCache.size, inFlight: frameWaiters.size })
    });
  }

  global.ArchiveXPreviewModules = global.ArchiveXPreviewModules || {};
  global.ArchiveXPreviewModules.createPreviewFrameCache = createPreviewFrameCache;
})(window);
