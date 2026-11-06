/* ============================================================================
 * FILE: js/player/live-seek-preview.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Dependencies and small helpers
 *   - Preview state
 *   - Seek overlay and geometry
 *   - Frame loading
 *   - Public controller
 * ============================================================================ */
/* Archive X live seek-preview module.
 * Owns live-preview frame requests, bubble geometry/rendering, and
 * cancellation. It never writes video.currentTime or
 * changes playback buffering, stream sources, or the seek scheduler.
 */
(function registerArchiveXLiveSeekPreview(global) {
  'use strict';

  function createLiveSeekPreview(deps) {

    // ------------------------------------------------------------------
    // SECTION: Dependencies and small helpers
    // Reads player context passed by the app.
    // ------------------------------------------------------------------
    const $ = deps.$;
    const getPlayer = deps.getPlayer;
    const getContext = deps.getContext;
    const getDuration = deps.getDuration;
    const isEnabled = deps.isEnabled;
    const isDragging = deps.isDragging;
    const keyFor = deps.keyFor;
    const formatDuration = deps.formatDuration;
    const getChapterTitleAt = typeof deps.getChapterTitleAt === 'function' ? deps.getChapterTitleAt : (() => '');
    const newAbortController = deps.newAbortController;

    // The details/watch-page redesign changed the player column's containing
    // blocks. Keep the preview bubble as a child of #dtop and calculate both
    // coordinates from viewport rectangles so it stays anchored to #pseek.
    function ensurePreviewPortal() {
      const box = $('pprev'), host = $('dtop');
      if (box && host && box.parentElement !== host) host.appendChild(box);
      return { box, host };
    }
    ensurePreviewPortal();


    // ------------------------------------------------------------------
    // SECTION: Preview state
    // Mutable preview state.
    // ------------------------------------------------------------------
    let active = false;
    let previewSequence = 0;
    let exactSequence = 0;
    let previewTimer = 0;
    let touchHideTimer = 0;
    let placeRaf = 0;
    let placeFraction = 0;
    let abortController = null;
    let busy = false;
    let candidateImage = null;
    let candidateLoadFinish = null;
    let slowTimer = 0;
    let wantedUrl = null;
    let wantedTime = null;
    let loadingUrl = '';
    let objectUrl = '';       // URL owned by this module for an exact live frame only.
    let candidateImageActive = false;
    let candidateSequence = 0;
    let lastGoodSrc = '';     // Most recent successfully decoded live-preview frame only.
    let exactTime = -1;
    let geometry = null;
    let seekOverlayToken = 0;
    let seekOverlayNode = null;
    let seekOverlayImage = null;
    let seekOverlayVideo = null;
    let seekOverlayFrameCallback = 0;
    let seekOverlayTimeout = 0;
    let seekOverlayFallbackHandler = null;
    let seekOverlaySettled = true;


    // ------------------------------------------------------------------
    // SECTION: Seek overlay and geometry
    // Retained frame and placement.
    // ------------------------------------------------------------------
    function clearRetainedFrame(token = seekOverlayToken) {
      if (token !== seekOverlayToken) return;
      seekOverlaySettled = true;
      clearTimeout(seekOverlayTimeout);
      seekOverlayTimeout = 0;
      const video = seekOverlayVideo;
      if (video && seekOverlayFrameCallback && typeof video.cancelVideoFrameCallback === 'function') {
        try { video.cancelVideoFrameCallback(seekOverlayFrameCallback); } catch (_) {}
      }
      seekOverlayFrameCallback = 0;
      if (video && seekOverlayFallbackHandler) {
        try {
          video.removeEventListener('seeked', seekOverlayFallbackHandler);
          video.removeEventListener('error', seekOverlayFallbackHandler);
        } catch (_) {}
      }
      seekOverlayFallbackHandler = null;
      seekOverlayNode?.remove();
      seekOverlayNode = null;
      seekOverlayImage = null;
      seekOverlayVideo = null;
    }

    function retainFrameOverPlayer(targetTime) {
      const video = getPlayer();
      const context = getContext();
      const host = $('pvisual');
      if (!video || !host || !context?.cid || !context?.mid) return false;
      clearRetainedFrame();

      // The hover bubble intentionally uses a small JPEG for low latency. Do
      // not stretch that 320px image over the full player during a committed
      // seek: it makes the whole video visibly blurry until the target decodes.
      // Capture the actual currently decoded video frame at intrinsic resolution
      // and hold it briefly instead. This is a transient live-frame hold, not a
      // storyboard/snapshot fallback and does not delay the seek request.
      const width = Math.max(0, Number(video.videoWidth) || 0);
      const height = Math.max(0, Number(video.videoHeight) || 0);
      if (Number(video.readyState || 0) < 2 || width < 2 || height < 2) return false;

      const overlay = document.createElement('div');
      overlay.id = 'pseek-frame-overlay';
      overlay.setAttribute('aria-hidden', 'true');
      Object.assign(overlay.style, {
        position: 'absolute', inset: '0', zIndex: '12', display: 'grid',
        placeItems: 'center', overflow: 'hidden', pointerEvents: 'none',
        background: 'rgba(0,0,0,.02)'
      });
      const frame = document.createElement('canvas');
      frame.width = width;
      frame.height = height;
      Object.assign(frame.style, {
        width: '100%', height: '100%', objectFit: 'contain',
        display: 'block', imageRendering: 'auto'
      });
      try {
        const ctx = frame.getContext('2d', { alpha: false });
        if (!ctx) return false;
        ctx.drawImage(video, 0, 0, width, height);
      } catch (_) {
        // If snapshotting is unavailable, skip the hold. Never fall back to
        // stretching the low-resolution seek-preview JPEG over the player.
        return false;
      }
      const token = ++seekOverlayToken;
      seekOverlaySettled = false;
      overlay.appendChild(frame);
      host.appendChild(overlay);
      seekOverlayNode = overlay;
      seekOverlayImage = frame;
      seekOverlayVideo = video;
      const target = Math.max(0, Number(targetTime) || 0);

      // Hold the sharp current frame until the browser presents a decoded frame
      // at the seek destination. Ignore callbacks for the old playhead.
      const videoFrame = (_now, metadata) => {
        if (token !== seekOverlayToken || seekOverlaySettled || video !== getPlayer()) return;
        const mediaTime = Number(metadata && metadata.mediaTime);
        const current = Number(video.currentTime);
        const observed = Number.isFinite(mediaTime) ? mediaTime : current;
        if (!video.seeking && Number.isFinite(observed) && Math.abs(observed - target) <= 0.75) {
          clearRetainedFrame(token);
          return;
        }
        if (typeof video.requestVideoFrameCallback === 'function') {
          try { seekOverlayFrameCallback = video.requestVideoFrameCallback(videoFrame); } catch (_) {}
        }
      };
      if (typeof video.requestVideoFrameCallback === 'function') {
        try { seekOverlayFrameCallback = video.requestVideoFrameCallback(videoFrame); } catch (_) {}
      } else {
        const fallback = () => {
          if (token !== seekOverlayToken || seekOverlaySettled || video !== getPlayer()) return;
          requestAnimationFrame(() => {
            if (token === seekOverlayToken && !seekOverlaySettled && !video.seeking && video.readyState >= 2) clearRetainedFrame(token);
          });
        };
        seekOverlayFallbackHandler = fallback;
        video.addEventListener('seeked', fallback);
        video.addEventListener('error', fallback);
      }
      // Do not leave an old frame covering the player if the seek cannot finish.
      seekOverlayTimeout = setTimeout(() => clearRetainedFrame(token), 5000);
      return true;
    }

    function geometryForPreview() {
      const video = getPlayer(), context = getContext();
      const touch = document.documentElement.getAttribute('data-ax-input') === 'touch' || window.innerWidth <= 640;
      let aspect = (video && video.videoWidth && video.videoHeight)
        ? video.videoWidth / video.videoHeight
        : (context.info && context.info.width && context.info.height ? context.info.width / context.info.height : 16 / 9);
      aspect = Math.max(.45, Math.min(2.4, aspect || 16 / 9));
      const maxWidth = touch ? 172 : 236, maxHeight = touch ? 190 : 236;
      let width = maxWidth, height = width / aspect;
      if (height > maxHeight) { height = maxHeight; width = height * aspect; }
      const sizeScale = 0.6; // 40% smaller while preserving the source aspect ratio.
      return { w: Math.round(width * sizeScale), h: Math.round(height * sizeScale) };
    }

    function place(fraction) {
      const { box, host } = ensurePreviewPortal();
      const seek = $('pseek');
      if (!box || !host || !seek) return;
      const size = geometry || (geometry = geometryForPreview());
      box.style.setProperty('--pp-w', size.w + 'px');
      box.style.setProperty('--pp-h', size.h + 'px');

      // Convert the target timeline point from viewport pixels into the player
      // shell's local CSS pixels. This deliberately does not assume that pseek
      // is the preview's offsetParent; responsive alignment/transforms may change it.
      const sr = seek.getBoundingClientRect();
      const hr = host.getBoundingClientRect();
      const sx = hr.width / Math.max(1, host.offsetWidth);
      const sy = hr.height / Math.max(1, host.offsetHeight);
      const originX = hr.left + host.clientLeft * sx;
      const originY = hr.top + host.clientTop * sy;
      const targetX = sr.left + Math.max(0, Math.min(1, Number(fraction) || 0)) * sr.width;
      const half = size.w / 2 + 6;
      const maxX = Math.max(half, host.clientWidth - half);
      const localX = (targetX - originX) / Math.max(.001, sx) + host.scrollLeft;
      const clampedX = Math.max(Math.min(half, host.clientWidth / 2), Math.min(maxX, localX));

      // Position above the actual seek bar, not by the percentage/height of a
      // parent that may have changed during the responsive layout refactor.
      const totalPreviewHeight = size.h + 34;
      const wantedTop = sr.top - totalPreviewHeight - 14;
      const localY = (wantedTop - originY) / Math.max(.001, sy) + host.scrollTop;
      const maxY = Math.max(4, host.clientHeight - totalPreviewHeight - 4);
      box.style.setProperty('left', clampedX + 'px', 'important');
      box.style.setProperty('top', Math.max(4, Math.min(maxY, localY)) + 'px', 'important');
      box.style.setProperty('bottom', 'auto', 'important');
    }


    // ------------------------------------------------------------------
    // SECTION: Frame loading
    // Scheduling, cancelling and loading frames.
    // ------------------------------------------------------------------
    function sameContext(context) {
      const now = getContext();
      return context.openTok === now.openTok && context.mediaKey === now.mediaKey &&
        context.mediaKey === keyFor(String(now.cid), Number(now.mid));
    }

    function scheduleLoad(delay, restart = false) {
      if (previewTimer) {
        if (!restart) return;
        clearTimeout(previewTimer);
        previewTimer = 0;
      }
      previewTimer = setTimeout(() => {
        previewTimer = 0;
        loadWanted();
      }, Math.max(0, Number(delay) || 0));
    }

    function hide() {
      active = false;
      ++previewSequence;
      ++exactSequence;
      wantedUrl = null;
      wantedTime = null;
      candidateImageActive = false;
      candidateSequence = 0;
      clearTimeout(previewTimer);
      previewTimer = 0;
      clearTimeout(slowTimer);
      slowTimer = 0;
      try { abortController?.abort(); } catch (_) { /* best-effort cancellation */ }
      cancelCandidateImage();
      busy = false;
      loadingUrl = '';
      exactTime = -1; // Match the shared player's prevExactT reset on hide.
      const box = $('pprev'), image = $('pprevimg');
      if (box) {
        box.style.display = 'none';
        box.classList.remove('busy', 'exact', 'live-frame', 'cached-frame', 'retained-frame', 'loading', 'slow', 'slow-loading', 'preview-error');
      }
      if (image) {
        image.onload = null;
        image.onerror = null;
        image.style.display = 'none';
      }
    }

    function cancelTimerOnly() {
      clearTimeout(previewTimer);
      previewTimer = 0;
    }

    function cancelQueued() {
      cancelTimerOnly();
      wantedUrl = null;
      wantedTime = null;
    }

    function cancelCandidateImage() {
      const finish = candidateLoadFinish;
      const pending = candidateImage;
      candidateLoadFinish = null;
      candidateImage = null;
      candidateImageActive = false;
      candidateSequence = 0;
      if (finish) {
        try { finish(false); } catch (_) { /* best-effort cancellation */ }
      }
      if (pending) {
        pending.onload = null;
        pending.onerror = null;
        try { pending.removeAttribute('src'); } catch (_) { try { pending.src = ''; } catch (__) {} }
      }
    }

    function cancelForSeek() {
      // A committed seek is the end of a preview gesture. Hide/abort it cleanly
      // rather than leaving an image element pointed at a cancelled request.
      hide();
    }

    function loadFrameAtTime(channelId, messageId, time, options = {}) {
      const targetTime = Math.max(0, Number(time) || 0);
      const requestedWidth = Number(options.width || 320);
      const width = Number.isFinite(requestedWidth) ? Math.max(240, Math.min(640, Math.round(requestedWidth))) : 320;
      const url = `/api/play/preview/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}?t=${encodeURIComponent(targetTime.toFixed(3))}&w=${width}&pv=15&lw=1&exact=1`;
      return new Promise((resolve, reject) => {
        const probe = new Image();
        let settled = false;
        let deadline = 0;
        const finish = ok => {
          if (settled) return;
          settled = true;
          clearTimeout(deadline);
          probe.onload = null;
          probe.onerror = null;
          if (ok && Number(probe.naturalWidth || 0) > 0) {
            resolve(probe);
          } else {
            try { probe.removeAttribute('src'); } catch (_) { /* best effort */ }
            reject(new Error('live seek preview image failed or timed out'));
          }
        };
        deadline = setTimeout(() => finish(false), 22000);
        probe.decoding = 'async';
        probe.onload = () => finish(true);
        probe.onerror = () => finish(false);
        probe.src = url;
      });
    }

    function suspendForSeek() {
      // A preview represents the pointer's timeline target, not video.currentTime.
      // Keep it alive across the single seek committed on pointer release; pointer
      // leave, video switch, or explicit hide still cancels it as usual.
      if (active || isDragging()) return;
      hide();
    }


    // ------------------------------------------------------------------
    // SECTION: Public controller
    // Preview, hide and clear.
    // ------------------------------------------------------------------
    function preview(fraction, immediate = false, previewOnly = false) {
      if (!isEnabled()) { hide(); return; }
      const duration = getDuration();
      if (!duration) return;
      fraction = Math.max(0, Math.min(1, Number(fraction) || 0));
      const exactTarget = !!(immediate && previewOnly);
      const time = exactTarget
        ? Math.max(0, Math.min(Math.max(0, duration - 0.5), fraction * duration))
        : Math.max(0, Math.min(duration, fraction * duration));
      const box = $('pprev'), image = $('pprevimg');
      const context = getContext();
      active = true;
      geometry = geometryForPreview();
      placeFraction = fraction;
      if (!placeRaf) placeRaf = requestAnimationFrame(() => { placeRaf = 0; place(placeFraction); });
      clearTimeout(touchHideTimer);
      ++previewSequence;
      if (box) box.style.display = 'block';

      // Ordinary pointer hover can share coarse preview buckets. A committed
      // release/keyboard target is requested at its exact timestamp so the frame
      // shown at commit and the browser currentTime use the same media time.
      const step = duration > 3600 ? 20 : duration > 1200 ? 10 : duration > 300 ? 5 : 2;
      const bucketTime = Math.min(duration, Math.round(time / step) * step);
      const frameTime = exactTarget ? time : bucketTime;
      const label = $('pprevt');
      const chapterTitle = String(getChapterTitleAt(frameTime) || '').trim();
      if (label) { label.textContent = chapterTitle ? `${formatDuration(frameTime) || '0:00'}  ·  ${chapterTitle}` : (formatDuration(frameTime) || '0:00'); label.title = chapterTitle ? `${formatDuration(frameTime) || '0:00'} · ${chapterTitle}` : ''; }
      const liveUrl = `/api/play/preview/${encodeURIComponent(context.cid)}/${context.mid}?t=${encodeURIComponent(frameTime.toFixed(3))}&w=320&pv=15&lw=1${exactTarget?'&exact=1':''}`;
      const url = liveUrl;

      if (exactTime === frameTime && box?.classList.contains('exact')) {
        wantedUrl = null;
        wantedTime = null;
        cancelTimerOnly();
        return;
      }
      if (url === loadingUrl) {
        // The in-flight request already targets this position. Remove the old
        // displayed frame so it can never be mistaken for the requested target.
        wantedUrl = null;
        wantedTime = null;
        cancelTimerOnly();
        lastGoodSrc = '';
        if (image) {
          image.onload = null;
          image.onerror = null;
          image.removeAttribute('src');
          image.style.display = 'none';
        }
        box?.classList.remove('exact', 'cached-frame', 'retained-frame', 'preview-error');
        box?.classList.add('loading');
        return;
      }

      // A previous hover frame belongs to a different timeline position. Clear
      // it immediately and keep the loading overlay visible until this target's
      // detached Image has successfully decoded.
      lastGoodSrc = '';
      if (image) {
        image.onload = null;
        image.onerror = null;
        image.removeAttribute('src');
        image.style.display = 'none';
      }
      box?.classList.remove('exact', 'cached-frame', 'retained-frame', 'preview-error');
      box?.classList.add('loading');

      wantedUrl = url;
      wantedTime = frameTime;
      // Ordinary hover uses a short debounce; while scrubbing, wait until the
      // user pauses for 500 ms before starting another network preview read.
      // The committed seek itself remains immediate and uses its own player tag.
      if (immediate && previewTimer) cancelTimerOnly();
      const dragging = isDragging();
      scheduleLoad(immediate ? 20 : (dragging ? Number(500) : 120), true);
    }

    async function loadWanted() {
      if (!isEnabled()) { wantedUrl = null; wantedTime = null; return; }
      const url = wantedUrl;
      if (!url) return;
      if (busy) return; // finally schedules the latest queued target when free
      const targetTime = wantedTime;
      wantedUrl = null;
      wantedTime = null;
      try { abortController?.abort(); } catch (_) { /* best-effort cancellation */ }
      abortController = newAbortController();
      if (!active) return;
      const context = getContext(), sequence = ++exactSequence, box = $('pprev'), image = $('pprevimg');
      busy = true;
      loadingUrl = url;
      if (box) box.classList.add('busy', 'loading', 'slow-loading');
      clearTimeout(slowTimer);
      slowTimer = setTimeout(() => {
        if (active && sequence === exactSequence && loadingUrl === url && box?.classList.contains('loading')) {
          box.classList.add('slow');
        }
      }, 4500);
      const stillCurrent = () => active && context.openTok === getContext().openTok &&
        sequence === exactSequence && context.mediaKey === getContext().mediaKey &&
        context.mediaKey === keyFor(String(getContext().cid), Number(getContext().mid));
      let probe = null;
      try {
        // Load the live preview as a native same-origin image request instead of
        // fetch()+blob. Browser request-interceptor extensions were cancelling the
        // fetch path before it reached Archive X's preview route. A detached Image
        // still lets us decode off-screen and only swap it in after it succeeds.
        probe = new Image();
        probe.decoding = 'async';
        candidateImage = probe;
        candidateImageActive = true;
        candidateSequence = sequence;
        const loaded = await new Promise(resolve => {
          let settled = false;
          let deadline = 0;
          const finish = ok => {
            if (settled) return;
            settled = true;
            clearTimeout(deadline);
            probe.onload = null;
            probe.onerror = null;
            if (candidateLoadFinish === finish) candidateLoadFinish = null;
            if (!ok) {
              try { probe.removeAttribute('src'); } catch (_) { try { probe.src = ''; } catch (__) {} }
            }
            resolve(!!ok);
          };
          candidateLoadFinish = finish;
          probe.onload = () => finish(Number(probe.naturalWidth || 0) > 0);
          probe.onerror = () => finish(false);
          // Give the live keyframe path and legacy live-frame renderer enough time,
          // but never leave the UI stuck on an infinite loading state.
          deadline = setTimeout(() => finish(false), 22000);
          probe.src = url;
        });
        if (candidateImage === probe) candidateImage = null;
        if (candidateSequence === sequence) candidateImageActive = false;
        if (!loaded) throw new Error('live preview image failed or timed out');
        if (!stillCurrent() || (wantedUrl && wantedUrl !== url)) return;
        // Transfer the already-loaded native Image node into the visible frame.
        // This avoids a second request and never exposes a broken image icon.
        const previousImage = $('pprevimg');
        probe.id = 'pprevimg';
        probe.alt = 'Live seek preview frame';
        probe.onload = null;
        probe.onerror = null;
        probe.style.display = 'block';
        if (previousImage && previousImage !== probe) previousImage.replaceWith(probe);
        else if (!previousImage) box?.querySelector('.pp-frame')?.appendChild(probe);
        lastGoodSrc = String(probe.src || url);
        objectUrl = '';
        if (box) {
          box.style.display = 'block';
          box.classList.remove('busy', 'loading', 'slow', 'slow-loading', 'cached-frame', 'retained-frame', 'preview-error', 'live-frame');
          box.classList.add('exact');
        }
        exactTime = Number(targetTime);
      } catch (error) {
        if (candidateImage === probe) candidateImage = null;
        if (candidateSequence === sequence) candidateImageActive = false;
        if (error?.name !== 'AbortError' && context.openTok === getContext().openTok &&
            sequence === exactSequence && (!wantedUrl || wantedUrl === url)) {
          box?.classList.remove('busy', 'loading', 'slow', 'slow-loading', 'exact', 'retained-frame');
          lastGoodSrc = '';
          image?.removeAttribute('src');
          if (image) image.style.display = 'none';
          box?.classList.add('preview-error');
        }
      } finally {
        if (candidateSequence === sequence) candidateImageActive = false;
        if (context.openTok === getContext().openTok && sequence === exactSequence) {
          busy = false;
          loadingUrl = '';
          clearTimeout(slowTimer);
          slowTimer = 0;
          box?.classList.remove('busy', 'slow', 'slow-loading');
          if (box?.classList.contains('exact')) {
            box.classList.remove('loading', 'retained-frame', 'cached-frame', 'preview-error');
          } else if (wantedUrl) {
            box?.classList.remove('preview-error', 'retained-frame');
            box?.classList.add('loading');
          } else {
            box?.classList.remove('loading', 'retained-frame');
            box?.classList.add('preview-error');
          }
        }
        if (wantedUrl) scheduleLoad(40);
      }
    }

    function scheduleHide(delay = 450) {
      clearTimeout(touchHideTimer);
      touchHideTimer = setTimeout(() => { if (!isDragging()) hide(); }, Math.max(0, Number(delay) || 0));
    }

    function clear() {
      hide();
      clearRetainedFrame();
      clearTimeout(touchHideTimer);
      touchHideTimer = 0;
      if (placeRaf) cancelAnimationFrame(placeRaf);
      placeRaf = 0;
      geometry = null;
      if (objectUrl) { try { URL.revokeObjectURL(objectUrl); } catch (_) { /* best-effort cleanup */ } }
      objectUrl = '';
      candidateImageActive = false;
      candidateSequence = 0;
      lastGoodSrc = '';
      const box = $('pprev'), image = $('pprevimg');
      image?.removeAttribute('src');
      if (box) box.classList.remove('exact', 'cached-frame', 'retained-frame', 'preview-error', 'loading');
    }

    return Object.freeze({
      preview,
      loadFrameAtTime,
      hide,
      clear,
      setHoverActive(value = true) { active = !!value; },
      cancelTimerOnly,
      cancelQueued,
      cancelForSeek,
      suspendForSeek,
      scheduleHide,
      retainFrameOverPlayer,
      clearRetainedFrame,
      getState: () => ({ active, busy, queued: !!wantedUrl, loading: !!loadingUrl, exactTime, hasLiveFrame: !!lastGoodSrc, seekFrameOverlay: !!seekOverlayNode })
    });
  }

  global.ArchiveXPreviewModules = global.ArchiveXPreviewModules || {};
  global.ArchiveXPreviewModules.createLiveSeekPreview = createLiveSeekPreview;
})(window);
