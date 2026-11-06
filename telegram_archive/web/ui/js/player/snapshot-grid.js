/* ============================================================================
 * FILE: js/player/snapshot-grid.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - State and helpers
 *   - Drawing and playback handoff
 *   - Loading and public controls
 * ============================================================================ */
/* Archive X 36-frame snapshot grid.
 * Owns grid data, progressive rendering and click-to-time interaction only.
 * It never creates a media source or mutates video.currentTime directly.
 */
(function registerArchiveXSnapshotGrid(global) {
  'use strict';

  const MAX_CANVAS_BACKING_PIXELS = 12_000_000;

  function getCanvasDpr(width, height, devicePixelRatio = global.devicePixelRatio) {
    const w = Math.max(1, Number(width) || 1);
    const h = Math.max(1, Number(height) || 1);
    const deviceDpr = Math.max(1, Math.min(3, Number(devicePixelRatio) || 1));
    const pixelBudgetDpr = Math.sqrt(MAX_CANVAS_BACKING_PIXELS / (w * h));
    return Math.min(deviceDpr, pixelBudgetDpr);
  }

  function createSnapshotGrid(deps) {

    // ------------------------------------------------------------------
    // SECTION: State and helpers
    // Dependencies and layout state.
    // ------------------------------------------------------------------
    const $ = deps.$;
    const state = deps.getPlayerState;
    const player = deps.getPlayer;
    const keyFor = deps.keyFor;
    const getVideo = deps.getVideo;
    const getDuration = deps.getDuration;
    const getManifest = deps.getManifest;
    const loadFrame = deps.loadFrame;
    const loadGridSprite = deps.loadGridSprite;
    const formatDuration = deps.formatDuration;
    const startPlayback = deps.startPlayback;
    const isVideoPlayingWithFrame = () => {
      const video = player();
      return !!video && video.paused === false && video.ended !== true && hasDrawableVideo();
    };
    const hasDrawableVideo = typeof deps.hasDrawableVideo === 'function'
      ? deps.hasDrawableVideo
      : () => {
          const el = player();
          const s = state();
          if (!el || Number(el.readyState || 0) < 2 || !(Number(el.videoWidth) > 0) || !(Number(el.videoHeight) > 0)) return false;
          const elementKey = String(el.dataset?.axMediaKey || '');
          return !elementKey || !s.mediaKey || elementKey === String(s.mediaKey);
        };
    let source = null;
    let grid = null;
    let loadSequence = 0;
    let loadingKey = '';
    let loadingPromise = null;
    let loadingFirstImage = null;
    let playbackStarting = false;
    let failureGridVisible = false;
    let startupIndex = 0;

    function layoutFor(width, height, frameAspect) {
      const W = Math.max(1, Number(width) || 1), H = Math.max(1, Number(height) || 1);
      const aspect = Math.max(.25, Math.min(4, Number(frameAspect) || 16 / 9));
      const possibleRows = [1, 2, 3, 4, 6, 9, 12, 18, 36];
      let best = null;
      for (const rows of possibleRows) {
        const cols = 36 / rows, tileW = W / cols, tileH = H / rows;
        const tileAspect = tileW / Math.max(1, tileH);
        const cropError = Math.abs(Math.log(Math.max(.001, tileAspect) / aspect));
        const sizePenalty = (tileW < 42 ? (42 - tileW) / 42 * .035 : 0) +
          (tileH < 34 ? (34 - tileH) / 34 * .035 : 0);
        const score = cropError + sizePenalty;
        if (!best || score < best.score) best = { rows, cols, score, tileW, tileH };
      }
      return best || { rows: 6, cols: 6 };
    }


    // ------------------------------------------------------------------
    // SECTION: Drawing and playback handoff
    // Canvas drawing and playback transitions.
    // ------------------------------------------------------------------
    function hide(options = {}) {
      cancelTileAnimations(grid);
      grid = null;
      const top = $('dtop');
      top?.classList.remove('preplay-snapshot-grid-on', 'preplay-snapshot-grid-fading', 'preplay-snapshot-grid-no-motion');
      if (!options.preserveStartup) {
        playbackStarting = false;
        failureGridVisible = false;
        top?.classList.remove('preplay-snapshot-starting', 'preplay-snapshot-failed');
      }
    }

    function frameRect(frame) {
      const image = frame?.image;
      if (!image) return null;
      if (image.__axGridSprite) {
        const cellW = Number(image.naturalWidth || image.width || 0) / 6;
        const cellH = Number(image.naturalHeight || image.height || 0) / 6;
        const index = Math.max(0, Math.min(35, Number(frame.spriteIndex ?? frame.index) || 0));
        if (cellW > 0 && cellH > 0) return { sx: (index % 6) * cellW, sy: Math.floor(index / 6) * cellH, sw: cellW, sh: cellH };
      }
      const fw = Number(image.naturalWidth || image.width || 0), fh = Number(image.naturalHeight || image.height || 0);
      return fw > 0 && fh > 0 ? { sx: 0, sy: 0, sw: fw, sh: fh } : null;
    }

    function drawImageCover(ctx, frame, x, y, w, h) {
      const image = frame?.image, rect = frameRect(frame);
      if (!image || !rect) return false;
      const scale = Math.max(w / rect.sw, h / rect.sh);
      const sw = w / scale, sh = h / scale;
      const sx = rect.sx + Math.max(0, (rect.sw - sw) / 2);
      const sy = rect.sy + Math.max(0, (rect.sh - sh) / 2);
      ctx.drawImage(image, sx, sy, Math.min(rect.sw, sw), Math.min(rect.sh, sh), x, y, w, h);
      return true;
    }

    function beginPlayback(time = 0) {
      const s = state(), top = $('dtop'), canvas = $('psbgrid'), host = $('pvisual');
      if (!source || source.key !== s.mediaKey || source.frames?.length !== 36 || !top || !canvas || !host) return false;
      const duration = Math.max(0, Number(s.dur || s.info?.duration || getDuration() || 0));
      const t = Math.max(0, Number(time) || 0);
      let index = 0;
      if (duration > 0) {
        const wanted = t / duration;
        index = source.frames.reduce((best, frame, i) => Math.abs((Number(frame.time) || 0) / duration - wanted) < Math.abs((Number(source.frames[best]?.time) || 0) / duration - wanted) ? i : best, 0);
      }
      if (!source.frames[index]?.image) {
        const nearest = source.frames.findIndex(frame => !!frame.image);
        if (nearest < 0) return false;
        index = nearest;
      }
      if (!frameRect(source.frames[index])) return false;
      cancelTileAnimations(grid);
      const rect = host.getBoundingClientRect();
      const W = Math.max(1, Math.round(rect.width)), H = Math.max(1, Math.round(rect.height));
      const dpr = getCanvasDpr(W, H);
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      const ctx = canvas.getContext('2d');
      if (!ctx) return false;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = '#050505'; ctx.fillRect(0, 0, W, H);
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
      if (!drawImageCover(ctx, source.frames[index], 0, 0, W, H)) return false;
      playbackStarting = true; failureGridVisible = false; startupIndex = index; grid = null;
      top.classList.remove('preplay-snapshot-grid-on', 'preplay-snapshot-grid-fading', 'preplay-snapshot-grid-no-motion', 'preplay-snapshot-failed');
      top.classList.add('preplay-snapshot-starting');
      return true;
    }

    function preserveForPlayback(key, time = 0) {
      if (!source || String(source.key) !== String(key)) return false;
      return beginPlayback(time);
    }

    function playbackStarted() {
      playbackStarting = false; failureGridVisible = false;
      finishTileAnimations(grid);
      const top = $('dtop');
      const showingStill = top?.classList.contains('preplay-snapshot-starting');
      const showingGrid = top?.classList.contains('preplay-snapshot-grid-on');
      if (showingStill || showingGrid) {
        // Keep the canvas mounted for the same short cross-fade whether it is
        // showing the 36-frame grid or the single startup still.
        top.classList.add('preplay-snapshot-grid-on');
        top.classList.remove('preplay-snapshot-starting', 'preplay-snapshot-failed');
        if (!top.classList.contains('preplay-snapshot-grid-fading')) {
          top.classList.add('preplay-snapshot-grid-fading');
          window.setTimeout(() => {
            top.classList.remove('preplay-snapshot-grid-on', 'preplay-snapshot-grid-fading');
          }, 280);
        }
      } else {
        top?.classList.remove('preplay-snapshot-starting', 'preplay-snapshot-failed', 'preplay-snapshot-grid-on', 'preplay-snapshot-grid-fading');
      }
      grid = null;
    }

    function playbackFailed(force = false) {
      if (!playbackStarting && !force) return;
      playbackStarting = false; failureGridVisible = !!force;
      const top = $('dtop');
      top?.classList.remove('preplay-snapshot-starting', 'preplay-snapshot-grid-fading');
      top?.classList.toggle('preplay-snapshot-failed', !!force);
      if (source?.key === state().mediaKey && source.frames?.length === 36) show(false, !!force);
    }

    function playbackStopped() {
      failureGridVisible = false;
      if (hasDrawableVideo()) return;
      playbackStarting = false;
      $('dtop')?.classList.remove('preplay-snapshot-starting', 'preplay-snapshot-failed', 'preplay-snapshot-grid-fading');
      if (source?.key === state().mediaKey && source.frames?.length === 36) show(false);
    }

    function drawTile(activeGrid, tile, progress = 1) {
      if (!activeGrid || !tile || !activeGrid.ctx) return;
      const ctx = activeGrid.ctx;
      const p = Math.max(0, Math.min(1, Number(progress) || 0));
      ctx.save?.();
      // Do not paint empty tile boxes, outlines, or time labels while stills
      // are arriving. The player stays clean until each actual image is ready.
      if (!frameRect(tile)) {
        ctx.restore?.();
        return;
      }
      // Redraw only this tile's clipped rectangle during its entrance. The
      // image and timestamp move together from the right while scaling in.
      ctx.beginPath();
      ctx.rect(tile.x, tile.y, tile.w, tile.h);
      ctx.clip();
      ctx.clearRect(tile.x, tile.y, tile.w, tile.h);
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#101010';
      ctx.fillRect(tile.x, tile.y, tile.w, tile.h);
      const scale = .3 + .7 * p;
      const offsetX = 40 * (1 - p);
      ctx.globalAlpha = p;
      ctx.translate(tile.x + tile.w / 2 + offsetX, tile.y + tile.h / 2);
      ctx.scale(scale, scale);
      if (!drawImageCover(ctx, tile, -tile.w / 2, -tile.h / 2, tile.w, tile.h)) {
        ctx.restore?.();
        return;
      }
      if (!window.matchMedia?.('(max-width: 599px)')?.matches) {
        const label = formatDuration(tile.time) || '0:00';
        ctx.font = '600 11px system-ui,sans-serif';
        const labelWidth = Math.ceil(ctx.measureText(label).width) + 8;
        if (tile.w > labelWidth + 8 && tile.h > 24) {
          const bx = tile.w / 2 - labelWidth - 5, by = tile.h / 2 - 19;
          ctx.fillStyle = 'rgba(0,0,0,.70)'; ctx.fillRect(bx, by, labelWidth, 15);
          ctx.fillStyle = '#fff'; ctx.fillText(label, bx + 4, by + 11);
        }
      }
      ctx.restore?.();
      if (!activeGrid.firstImagePainted && p > 0) {
        activeGrid.firstImagePainted = true;
        const callback = activeGrid.onFirstImage;
        activeGrid.onFirstImage = null;
        if (source?.key === activeGrid.key) source.onFirstImage = null;
        if (typeof callback === 'function') {
          try { callback(); } catch (_) {}
        }
      }
    }

    function animateGridTiles(activeGrid, now) {
      activeGrid.rafId = 0;
      if (grid !== activeGrid || source?.key !== activeGrid.key) return;
      for (const tile of [...activeGrid.animatingTiles]) {
        const progress = Math.max(0, Math.min(1, (now - tile.entryStartAt) / 600));
        if (progress >= 1) {
          drawTile(activeGrid, tile, 1);
          tile.entryAnimationComplete = true;
          activeGrid.animatingTiles.delete(tile);
        } else if (progress > 0) {
          drawTile(activeGrid, tile, progress);
        }
      }
      if (activeGrid.animatingTiles.size) {
        activeGrid.rafId = window.requestAnimationFrame(time => animateGridTiles(activeGrid, time));
      }
    }

    function renderTile(activeGrid, tile) {
      if (!activeGrid || !tile || !frameRect(tile)) return;
      if (!activeGrid.animateTiles || tile.entryAnimationComplete) {
        tile.entryAnimationComplete = true;
        drawTile(activeGrid, tile, 1);
        return;
      }
      // Match the requested CSS choreography: row-major index × 50 ms,
      // 600 ms linear motion, 40 px from the right, and 30% → 100% scale.
      tile.entryStartAt = activeGrid.animationStartAt + Number(tile.index || 0) * 50;
      const now = window.performance.now();
      const progress = Math.max(0, Math.min(1, (now - tile.entryStartAt) / 600));
      if (progress >= 1) {
        tile.entryAnimationComplete = true;
        drawTile(activeGrid, tile, 1);
        return;
      }
      activeGrid.animatingTiles.add(tile);
      drawTile(activeGrid, tile, progress);
      if (!activeGrid.rafId) {
        activeGrid.rafId = window.requestAnimationFrame(time => animateGridTiles(activeGrid, time));
      }
    }

    function finishTileAnimations(activeGrid) {
      if (!activeGrid) return;
      for (const tile of activeGrid.animatingTiles) {
        drawTile(activeGrid, tile, 1);
        tile.entryAnimationComplete = true;
      }
      cancelTileAnimations(activeGrid);
    }

    function cancelTileAnimations(activeGrid) {
      if (!activeGrid) return;
      if (activeGrid.rafId) window.cancelAnimationFrame?.(activeGrid.rafId);
      activeGrid.rafId = 0;
      activeGrid.animatingTiles?.clear();
    }

    function redrawTile(index) {
      if (!source || !source.frames?.[index]) return;
      if (!grid || grid.key !== source.key || !grid.tiles?.[index]) return;
      if (isVideoPlayingWithFrame() && !failureGridVisible) { hide(); return; }
      const tile = grid.tiles[index];
      tile.image = source.frames[index]?.image || null;
      if (tile.image) {
        $('dtop')?.classList.add('preplay-snapshot-grid-on');
        renderTile(grid, tile);
      }
    }


    // ------------------------------------------------------------------
    // SECTION: Loading and public controls
    // Loading, visibility, clicks and teardown.
    // ------------------------------------------------------------------
    function show(animate = true, force = false) {
      const s = state(), top = $('dtop'), canvas = $('psbgrid'), host = $('pvisual');
      if (playbackStarting || !source || source.frames?.length !== 36 || !top || !canvas || !host || source.key !== s.mediaKey) return;
      if (isVideoPlayingWithFrame() && !force && !failureGridVisible) { hide(); return; }
      cancelTileAnimations(grid);
      const rect = host.getBoundingClientRect();
      const W = Math.max(1, Math.round(rect.width)), H = Math.max(1, Math.round(rect.height));
      const dpr = getCanvasDpr(W, H);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = '#101010'; ctx.fillRect(0, 0, W, H);
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
      const aspect = Math.max(.25, Math.min(4, (source.fw || 16 / 9) / Math.max(1, source.fh || 1)));
      const layout = layoutFor(W, H, aspect), rows = layout.rows, cols = layout.cols;
      const tiles = source.frames.map((frame, index) => {
        const row = Math.floor(index / cols), col = index % cols;
        const x0 = Math.round(col * W / cols), x1 = Math.round((col + 1) * W / cols);
        const y0 = Math.round(row * H / rows), y1 = Math.round((row + 1) * H / rows);
        return { ...frame, x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
      });
      const prefersReducedMotion = !!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
      const animateTiles = !!animate && !prefersReducedMotion;
      top.classList.toggle('preplay-snapshot-grid-no-motion', !animateTiles);
      const readyCount = source.frames.reduce((n, frame) => n + (frame.image ? 1 : 0), 0);
      const activeGrid = {
        key: source.key, rows, cols, W, H, fw: source.fw, fh: source.fh, ctx, tiles,
        onFirstImage: source.onFirstImage || null, firstImagePainted: false,
        animateTiles, animationStartAt: window.performance.now(), animatingTiles: new Set(), rafId: 0
      };
      grid = activeGrid;
      if (readyCount > 0 || force) top.classList.add('preplay-snapshot-grid-on');
      else top.classList.remove('preplay-snapshot-grid-on');
      // Animate only the tiles currently entering, sharing one RAF loop rather
      // than redrawing the full 36-frame canvas on every animation frame.
      tiles.forEach(tile => {
        if (tile.image) renderTile(activeGrid, tile);
      });
    }

    async function loadSnapshot(cid, mid, force, key, onFirstImage = null) {
      const sequence = ++loadSequence;
      const current = () => {
        const s = state();
        return sequence === loadSequence && s.mediaKey === key &&
          String(s.cid) === String(cid) && Number(s.mid) === Number(mid);
      };
      if (!cid || mid == null || mid === '' || !key) return;
      // Use the in-memory snapshot set first. Reopening the same video should not
      // revalidate its manifest or request the 36 cached JPEGs again.
      if (!force && source?.key === key && source.frames?.length === 36) {
        source.onFirstImage = onFirstImage;
        if (!isVideoPlayingWithFrame()) show(true);
        return;
      }
      hide({ preserveStartup: playbackStarting });
      try {
        const manifest = await getManifest(cid, mid, !!force);
        if (!current()) return;
        if (!manifest || manifest.frames_ready !== true || Number(manifest.frame_count || 0) < 36) return;
        const revision = String(manifest.revision || manifest.updated_at || manifest.version || '');
        if (!force && source?.key === key && source.frames?.length === 36 && String(source.revision || '') === revision) {
          source.onFirstImage = onFirstImage;
          if (!isVideoPlayingWithFrame()) show(true);
          return;
        }
        let times = Array.isArray(manifest.frame_times) && manifest.frame_times.length === 36
          ? manifest.frame_times.map(Number) : [];
        if (times.length !== 36 || times.some(t => !Number.isFinite(t) || t < 0)) {
          const video = getVideo();
          const duration = Math.max(0, Number(state().dur || state().info?.duration || video?.duration || getDuration() || 0));
          times = Array.from({ length: 36 }, (_, i) => duration > 0 ? duration * (.02 + .96 * i / 35) : i);
        }
        source = {
          key, revision, onFirstImage,
          fw: Number(manifest.frame_width || manifest.width || 320),
          fh: Number(manifest.frame_height || manifest.height || 180),
          frames: Array.from({ length: 36 }, (_, index) => ({ index, time: Math.max(0, Number(times[index]) || 0), image: null, loaded: false }))
        };
        // Keep an empty grid hidden. A sprite-ready video becomes visible only
        // after its complete 36-frame image has loaded and decoded; legacy
        // individual frames can still appear as their requests finish.
        if (!isVideoPlayingWithFrame()) show(true);

        // New caches provide all 36 validated local stills in one decoded
        // sprite request. Do not fetch frame 0 separately while it loads.
        // Legacy complete sets remain readable using their independent files.
        let sprite = null;
        if (manifest.sprite_ready === true && typeof loadGridSprite === 'function') {
          try { sprite = await loadGridSprite(cid, mid, manifest); } catch (_) { sprite = null; }
        }
        if (!current() || source?.key !== key || source?.revision !== revision) return;
        if (sprite) {
          source.frames.forEach((frame, index) => {
            frame.image = sprite; frame.spriteIndex = index; frame.loaded = true;
          });
          if (!isVideoPlayingWithFrame()) show(true);
        } else {
          const callerCache = new Map(), waiters = new Map();
          let nextIndex = 0;
          const worker = async () => {
            while (nextIndex < 36) {
              const index = nextIndex++;
              let image = null;
              try { image = await loadFrame(cid, mid, index, callerCache, waiters, 36, revision); }
              catch (_) { image = null; }
              if (!current() || source?.key !== key || source?.revision !== revision) return;
              source.frames[index].image = image || null;
              source.frames[index].loaded = true;
              if (image) redrawTile(index);
            }
          };
          await Promise.all(Array.from({ length: 8 }, () => worker()));
        }
        if (!current() || source?.key !== key) return;
        if (!source.frames.some(frame => frame.image) && !isVideoPlayingWithFrame()) {
          // Keep the frame slots and tell the user why there is no imagery instead
          // of leaving a content-free black player with no feedback.
          show(false, true);
          const activeGrid = grid;
          if (activeGrid) {
            const ctx = activeGrid.ctx;
            ctx.fillStyle = 'rgba(0,0,0,.72)';
            ctx.fillRect(Math.max(0, activeGrid.W / 2 - 150), Math.max(0, activeGrid.H / 2 - 20), Math.min(300, activeGrid.W), 40);
            ctx.fillStyle = '#e8e8e8'; ctx.font = '600 12px system-ui,sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText('Snapshots unavailable — rebuild snapshots', activeGrid.W / 2, activeGrid.H / 2);
            ctx.textAlign = 'start'; ctx.textBaseline = 'alphabetic';
          }
        }
      } catch (_) {
        if (current()) hide();
      }
    }

    function load(cid, mid, force = false, onFirstImage = null) {
      const key = keyFor(cid, mid);
      if (!cid || mid == null || mid === '' || !key) return Promise.resolve();
      if (!force && loadingKey === key && loadingPromise) {
        if (typeof onFirstImage === 'function') loadingFirstImage = onFirstImage;
        return loadingPromise;
      }
      loadingFirstImage = typeof onFirstImage === 'function' ? onFirstImage : null;
      const notifyFirstImage = () => {
        const callback = loadingFirstImage;
        loadingFirstImage = null;
        if (typeof callback === 'function') callback();
      };
      const promise = loadSnapshot(cid, mid, force, key, notifyFirstImage);
      loadingKey = key;
      loadingPromise = promise;
      return promise.finally(() => {
        if (loadingPromise === promise) {
          loadingKey = ''; loadingPromise = null;
          // A sprite-ready load can finish before its staggered first tile is
          // painted. Keep its callback alive until drawTile notifies it.
          if (source?.key !== key || !source.frames?.some(frame => frame.image)) loadingFirstImage = null;
        }
      });
    }

    function syncVisibility(event = null) {
      const s = state(), top = $('dtop'), video = player();
      if (failureGridVisible && top?.classList.contains('preplay-snapshot-grid-on')) return;
      // The browser's `playing` event is authoritative even when videoWidth or
      // readyState has not caught up yet. Release the snapshot layer on this
      // event so it can never remain over a video that has started playing.
      if (event?.type === 'playing' && video && video.paused === false && video.ended !== true) {
        playbackStarted();
        return;
      }
      if (isVideoPlayingWithFrame()) {
        if (playbackStarting || top?.classList.contains('preplay-snapshot-grid-on')) playbackStarted();
        return;
      }
      // A Play click owns the startup poster until a real decoded video frame or
      // a terminal stop/error. Do not let the old grid flash back over it.
      if (playbackStarting || top?.classList.contains('preplay-snapshot-starting')) return;
      if (source?.key === s.mediaKey && source.frames?.length === 36 && !top?.classList.contains('preplay-snapshot-grid-on')) show(false);
    }

    function click(event) {
      const activeGrid = grid, canvas = $('psbgrid'), video = getVideo(), s = state();
      if (!activeGrid || activeGrid.tiles?.length !== 36 || !canvas || !video || activeGrid.key !== s.mediaKey) return;
      const rect = canvas.getBoundingClientRect();
      const x = (event.clientX - rect.left) * (activeGrid.W / Math.max(1, rect.width));
      const y = (event.clientY - rect.top) * (activeGrid.H / Math.max(1, rect.height));
      const tile = activeGrid.tiles.find(item => x >= item.x && x < item.x + item.w && y >= item.y && y < item.y + item.h);
      if (!tile || !tile.image) return;
      const timestamp = Math.max(0, Number(tile.time) || 0);
      s.pendingStartAt = timestamp;
      startPlayback(video.id, video._cid, video.title || video.name, false, { choice: 'beginning', stored: 0 }).catch(() => {});
    }

    function destroy() { hide(); source = null; loadSequence += 1; loadingKey = ''; loadingPromise = null; loadingFirstImage = null; }
    return Object.freeze({ hide, load, show, click, syncVisibility, destroy, beginPlayback, preserveForPlayback, playbackStarted, playbackFailed, playbackStopped,
      getState: () => ({ hasSource: !!source, key: source?.key || '', frameCount: source?.frames?.length || 0,
        loadedFrameCount: source?.frames?.reduce((n, f) => n + (f.image ? 1 : 0), 0) || 0,
        renderedFrameCount: grid?.tiles?.length || 0, isLoading: !!loadingPromise }) });
  }

  global.ArchiveXPreviewModules = global.ArchiveXPreviewModules || {};
  global.ArchiveXPreviewModules.createSnapshotGrid = createSnapshotGrid;
  global.ArchiveXPreviewModules.getCanvasDpr = getCanvasDpr;
})(window);
