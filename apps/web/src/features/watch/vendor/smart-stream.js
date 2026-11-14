/* ============================================================================
 * FILE: js/player/smart-stream.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Capability checks and codec mapping
 *   - Route decision
 *   - Pipeline 1: MP4Box to MSE
 *   - Pipeline 2: mpegts to MSE
 *   - Pipeline 3: mediabunny
 *   - Public API
 * ============================================================================ */
/* Archive X Smart Streaming Engine v1.0
 * Client-side MSE/WebCodecs pipeline — plays any codec without server-side FFmpeg.
 *
 * Pipeline routing:
 *   MP4 (H.264/H.265/AV1/VP9 + AAC/Opus)  → mp4box.js demux → MSE SourceBuffer
 *   MPEG-TS                                 → mpegts.js transmux to fMP4 → MSE
 *   MKV/WebM                                → mediabunny demux → MSE or WebCodecs
 *   Fallback                                → server-side FFmpeg compat stream
 *
 * The backend stays a dumb byte-pipe (/stream/{cid}/{mid}). All codec handling
 * is done here in the browser.
 */
(function registerSmartStream(global) {
  'use strict';


  // ------------------------------------------------------------------
  // SECTION: Capability checks and codec mapping
  // Library checks and MSE MIME mapping.
  // ------------------------------------------------------------------
  // ── Library availability checks ──
  const hasMP4Box    = () => typeof global.MP4Box !== 'undefined';
  const hasMpegts    = () => typeof global.mpegts !== 'undefined';
  const hasMediaBunny= () => typeof global.MediaBunny !== 'undefined' || typeof global.mediabunny !== 'undefined';
  const hasMSE       = () => typeof MediaSource !== 'undefined' && typeof MediaSource.isTypeSupported === 'function';
  const hasManagedMSE= () => typeof ManagedMediaSource !== 'undefined';
  const hasWebCodecs = () => typeof VideoDecoder !== 'undefined' && typeof EncodedVideoChunk !== 'undefined';
  // ── Codec MIME mapping for MSE ──
  const VIDEO_CODECS = {
    h264:  'avc1.640028',      // High profile, level 4.0
    avc:   'avc1.640028',
    avc1:  'avc1.640028',
    hevc:  'hev1.1.6.L120.B0', // Main profile
    h265:  'hev1.1.6.L120.B0',
    vp9:   'vp09.00.31.08',    // Profile 0, level 3.1, 8-bit
    vp8:   'vp8',
    av1:   'av01.0.08M.08',    // Main profile, level 4.0, 8-bit
    mpeg4: 'mp4v.20.9',
  };
  const AUDIO_CODECS = {
    aac:   'mp4a.40.2',        // AAC-LC
    opus:  'opus',
    mp3:   'mp4a.69',
    ac3:   'ac-3',
    eac3:  'ec-3',
    flac:  'flac',
    vorbis:'vorbis',
  };

  function mseMimeFor(vcodec, acodec, container) {
    const vc = (vcodec || '').toLowerCase().replace(/[.\-_ ]/g, '');
    const ac = (acodec || '').toLowerCase().replace(/[.\-_ ]/g, '');
    const vMime = VIDEO_CODECS[vc] || '';
    const aMime = AUDIO_CODECS[ac] || '';
    // For MSE, we always feed fMP4 segments
    const codecs = [vMime, aMime].filter(Boolean).join(', ');
    if (!codecs) return '';
    return `video/mp4; codecs="${codecs}"`;
  }


  // ------------------------------------------------------------------
  // SECTION: Route decision
  // Chooses a streaming pipeline.
  // ------------------------------------------------------------------
  // ── Capability detection ──
  function canMSEPlay(vcodec, acodec) {
    if (!hasMSE() && !hasManagedMSE()) return false;
    const mime = mseMimeFor(vcodec, acodec);
    if (!mime) return false;
    const MSEClass = hasMSE() ? MediaSource : ManagedMediaSource;
    return MSEClass.isTypeSupported(mime);
  }

  // ── Route decision ──
  // Returns 'mse-mp4box' | 'mse-mpegts' | 'mse-mediabunny' | 'webcodecs' | null
  function chooseSmartPipeline(info) {
    if (!info) return null;
    const name  = String(info.name || '').toLowerCase();
    const fmt   = String(info.format || '').toLowerCase();
    const mime  = String(info.mime || '').toLowerCase();
    const vc    = String(info.vcodec || '').toLowerCase();
    const ac    = String(info.acodec || '').toLowerCase();

    // Already plays natively? No smart pipeline needed.
    if (info.direct_likely) return null;

    const isMP4  = name.endsWith('.mp4') || name.endsWith('.m4v') || name.endsWith('.mov') ||
                   fmt.includes('mp4') || fmt.includes('mov') || mime === 'video/mp4' || mime === 'video/quicktime';
    const isTS   = name.endsWith('.ts') || name.endsWith('.mts') || name.endsWith('.m2ts') ||
                   fmt.includes('mpegts') || mime === 'video/mp2t';
    const isMKV  = name.endsWith('.mkv') || fmt.includes('matroska') || mime === 'video/x-matroska';
    const isWebM = name.endsWith('.webm') || fmt.includes('webm') || mime === 'video/webm';
    const isFLV  = name.endsWith('.flv') || fmt.includes('flv') || mime === 'video/x-flv';
    const isAVI  = name.endsWith('.avi') || fmt.includes('avi') || mime === 'video/x-msvideo';
    const isWMV  = name.endsWith('.wmv') || fmt.includes('asf') || mime === 'video/x-ms-wmv';

    // Check if the browser's MSE can handle these codecs in fMP4
    const mseOK = canMSEPlay(vc, ac);

    // MP4 container with MSE-supported codecs → mp4box.js progressive fragmentation
    if (isMP4 && mseOK && hasMP4Box()) return 'mse-mp4box';

    // MPEG-TS → mpegts.js transmux to fMP4
    if ((isTS || isFLV) && hasMpegts()) return 'mse-mpegts';

    // MKV/WebM/AVI with MSE codecs → mediabunny demux
    if ((isMKV || isWebM || isAVI || isWMV) && mseOK && hasMediaBunny()) return 'mse-mediabunny';

    // MP4 but MSE can't handle the codec → try mp4box.js + WebCodecs
    if (isMP4 && hasWebCodecs() && hasMP4Box()) return 'webcodecs';

    // Any container with mediabunny + WebCodecs
    if (hasMediaBunny() && (hasWebCodecs() || mseOK)) return 'mse-mediabunny';

    return null;
  }


  // ------------------------------------------------------------------
  // SECTION: Pipeline 1: MP4Box to MSE
  // ------------------------------------------------------------------
  // ════════════════════════════════════════════
  //  Pipeline 1: MP4Box.js → MSE
  // ════════════════════════════════════════════
  function createMP4BoxMSEPipeline(videoEl, streamUrl, info, opts = {}) {
    const vc = String(info.vcodec || '').toLowerCase().replace(/[.\-_ ]/g, '');
    const ac = String(info.acodec || '').toLowerCase().replace(/[.\-_ ]/g, '');
    const mseType = mseMimeFor(info.vcodec, info.acodec);
    if (!mseType) return null;

    let mp4box = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let fetchController = null;
    let destroyed = false;
    let pendingSegments = [];
    let sbUpdating = false;
    let fileOffset = 0;
    let initSegReceived = false;
    let totalBytesLoaded = 0;

    const MSEClass = hasMSE() ? MediaSource : ManagedMediaSource;

    function feedNextSegment() {
      if (destroyed || !sourceBuffer || sbUpdating || pendingSegments.length === 0) return;
      sbUpdating = true;
      const seg = pendingSegments.shift();
      try {
        sourceBuffer.appendBuffer(seg);
      } catch (e) {
        // QuotaExceededError: remove old buffered data and retry
        if (e.name === 'QuotaExceededError' && sourceBuffer.buffered.length > 0) {
          const removeEnd = Math.max(0, sourceBuffer.buffered.start(0));
          try { sourceBuffer.remove(0, removeEnd); } catch (_) {}
          pendingSegments.unshift(seg);
        }
        sbUpdating = false;
      }
    }

    function init() {
      mediaSource = new MSEClass();
      const objectURL = URL.createObjectURL(mediaSource);
      videoEl.src = objectURL;

      mediaSource.addEventListener('sourceopen', () => {
        if (destroyed) return;
        try {
          sourceBuffer = mediaSource.addSourceBuffer(mseType);
          sourceBuffer.mode = 'segments';
        } catch (e) {
          // Fallback: try without specifying mode
          try { sourceBuffer = mediaSource.addSourceBuffer(mseType); } catch (_) {
            opts.onError?.('MSE addSourceBuffer failed: ' + e.message);
            return;
          }
        }
        sourceBuffer.addEventListener('updateend', () => {
          sbUpdating = false;
          feedNextSegment();
          // Auto-remove old buffer to prevent QuotaExceeded
          if (sourceBuffer.buffered.length > 0) {
            const current = videoEl.currentTime;
            const start = sourceBuffer.buffered.start(0);
            if (current - start > 120) {
              try { sourceBuffer.remove(start, current - 60); } catch (_) {}
            }
          }
        });
        sourceBuffer.addEventListener('error', (e) => {
          opts.onError?.('SourceBuffer error');
        });
        startFetch();
      });

      mediaSource.addEventListener('sourceended', () => {});
      mediaSource.addEventListener('sourceclose', () => { destroy(); });
    }

    function startFetch() {
      mp4box = global.MP4Box.createFile();

      mp4box.onReady = function(boxInfo) {
        if (destroyed) return;
        // Initialize segmentation — tells mp4box to produce fMP4 segments
        const trackOpts = {};
        for (const track of boxInfo.tracks) {
          trackOpts[track.id] = {
            nbSamples: 100, // samples per segment
          };
        }
        mp4box.setSegmentOptions(boxInfo.tracks[0].id, null, { nbSamples: 100 });
        if (boxInfo.tracks.length > 1) {
          mp4box.setSegmentOptions(boxInfo.tracks[1].id, null, { nbSamples: 100 });
        }
        const initSegs = mp4box.initializeSegmentation();
        for (const initSeg of initSegs) {
          pendingSegments.push(initSeg.buffer);
        }
        initSegReceived = true;
        feedNextSegment();
        mp4box.start();
        if (pendingSeek !== null) { const t = pendingSeek; pendingSeek = null; seekTo(t); }
      };

      mp4box.onSegment = function(id, user, buffer, sampleNum, isLast) {
        if (destroyed) return;
        pendingSegments.push(buffer);
        feedNextSegment();
        if (isLast && mediaSource.readyState === 'open') {
          // Wait for pending segments to flush before ending
          const checkEnd = () => {
            if (pendingSegments.length === 0 && !sbUpdating) {
              try { mediaSource.endOfStream(); } catch (_) {}
            } else {
              setTimeout(checkEnd, 100);
            }
          };
          setTimeout(checkEnd, 200);
        }
      };

      mp4box.onError = function(e) {
        if (!destroyed) opts.onError?.('MP4Box parse error: ' + e);
      };

      fetchFrom(0);
    }

    /** Read the file from byte `offset` on (a range request; the first read starts at 0). Whatever was being read before is dropped. */
    function fetchFrom(offset) {
      try { fetchController?.abort(); } catch (_) {}
      const controller = new AbortController();
      fetchController = controller;
      fileOffset = offset;
      const init = { signal: controller.signal, credentials: 'include' };
      if (offset > 0) init.headers = { Range: 'bytes=' + offset + '-' };
      fetch(streamUrl, init)
        .then(response => {
          if (!response.ok) throw new Error('Stream HTTP ' + response.status);
          if (offset > 0 && response.status !== 206) throw new Error('The server did not honour the range request');
          if (!response.body) throw new Error('No readable stream');
          const reader = response.body.getReader();
          return pump(reader, controller);
        })
        .catch(e => {
          if (!destroyed && e.name !== 'AbortError') {
            opts.onError?.('Fetch error: ' + e.message);
          }
        });
    }

    /** Is the moment already buffered (with a little to play)? Then the browser can seek on its own. */
    function isBuffered(time) {
      try {
        const b = sourceBuffer.buffered;
        for (let i = 0; i < b.length; i++) if (time >= b.start(i) - 0.05 && time < b.end(i) - 0.5) return true;
      } catch (_) {}
      return false;
    }

    let pendingSeek = null;
    /** Jump to a moment that is not buffered: ask the file where that moment starts (the keyframe before it) and read from there. */
    function seekTo(time) {
      if (destroyed) return;
      if (!mp4box || !sourceBuffer || !initSegReceived) { pendingSeek = time; return; }
      if (isBuffered(time)) return;
      try {
        mp4box.stop();
        pendingSegments = [];
        const where = mp4box.seek(time, true);
        mp4box.start();
        fetchFrom(Math.max(0, Number(where && where.offset) || 0));
      } catch (e) {
        opts.onError?.('Seek error: ' + (e && e.message));
      }
    }

    function pump(reader, controller) {
      return reader.read().then(({ done, value }) => {
        if (destroyed || controller !== fetchController) { try { reader.cancel(); } catch (_) {} return; }   // a newer read (after a seek) took over
        if (done) {
          if (mp4box) { mp4box.flush(); }
          return;
        }
        // mp4box expects ArrayBuffer with fileStart property
        const buf = value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
        buf.fileStart = fileOffset;
        fileOffset += buf.byteLength;
        totalBytesLoaded += buf.byteLength;
        opts.onProgress?.(totalBytesLoaded, Number(info.size) || 0);
        try {
          mp4box.appendBuffer(buf);
        } catch (e) {
          opts.onError?.('MP4Box append error: ' + e.message);
          reader.cancel();
          return;
        }
        return pump(reader, controller);
      });
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      try { fetchController?.abort(); } catch (_) {}
      try {
        if (mediaSource && mediaSource.readyState === 'open') mediaSource.endOfStream();
      } catch (_) {}
      try { mp4box?.flush(); } catch (_) {}
      mp4box = null;
      mediaSource = null;
      sourceBuffer = null;
      pendingSegments = [];
    }

    init();
    return { seekTo, destroy, type: 'mse-mp4box' };
  }


  // ------------------------------------------------------------------
  // SECTION: Pipeline 2: mpegts to MSE
  // ------------------------------------------------------------------
  // ════════════════════════════════════════════
  //  Pipeline 2: mpegts.js → MSE
  // ════════════════════════════════════════════
  function createMpegtsMSEPipeline(videoEl, streamUrl, info, opts = {}) {
    let player = null;
    let destroyed = false;

    function init() {
      if (!global.mpegts || !global.mpegts.isSupported()) {
        opts.onError?.('mpegts.js not supported in this browser');
        return;
      }

      const type = String(info.name || '').toLowerCase().endsWith('.flv') ? 'flv' : 'mpegts';

      player = global.mpegts.createPlayer({
        type: type,
        url: streamUrl,
        isLive: false,
        hasAudio: !!info.acodec,
        hasVideo: !!info.vcodec,
      }, {
        enableWorker: true,
        enableStashBuffer: true,
        stashInitialSize: 1024 * 384,       // 384KB initial buffer
        lazyLoad: true,
        lazyLoadMaxDuration: 180,            // 3 min ahead
        lazyLoadRecoverDuration: 30,
        seekType: 'range',
        reuseRedirectedURL: true,
        liveBufferLatencyChasing: false,
      });

      player.attachMediaElement(videoEl);

      player.on(global.mpegts.Events.ERROR, (errType, errDetail, errInfo) => {
        if (!destroyed) opts.onError?.(`mpegts error: ${errType} / ${errDetail}`);
      });

      player.on(global.mpegts.Events.LOADING_COMPLETE, () => {
        opts.onComplete?.();
      });

      player.on(global.mpegts.Events.STATISTICS_INFO, (stats) => {
        if (stats.speed) opts.onProgress?.(stats.totalSegmentCount || 0, 0);
      });

      player.load();
    }

    function seekTo(time) {
      if (player && !destroyed) {
        videoEl.currentTime = time;
      }
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      try {
        player?.pause();
        player?.unload();
        player?.detachMediaElement();
        player?.destroy();
      } catch (_) {}
      player = null;
    }

    init();
    return { seekTo, destroy, type: 'mse-mpegts' };
  }


  // ------------------------------------------------------------------
  // SECTION: Pipeline 3: mediabunny
  // ------------------------------------------------------------------
  // ════════════════════════════════════════════
  //  Pipeline 3: mediabunny → MSE/WebCodecs
  // ════════════════════════════════════════════
  function createMediaBunnyPipeline(videoEl, streamUrl, info, opts = {}) {
    const MB = global.MediaBunny || global.mediabunny;
    let reader = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let fetchController = null;
    let destroyed = false;
    let pendingSegments = [];
    let sbUpdating = false;
    const MSEClass = hasMSE() ? MediaSource : (hasManagedMSE() ? ManagedMediaSource : null);
    const mseType = mseMimeFor(info.vcodec, info.acodec);

    function init() {
      if (!MB) {
        opts.onError?.('mediabunny not available');
        return;
      }

      // Try MSE path first
      if (MSEClass && mseType && MSEClass.isTypeSupported(mseType)) {
        initMSEPath();
      } else if (hasWebCodecs()) {
        initWebCodecsPath();
      } else {
        opts.onError?.('Neither MSE nor WebCodecs available for this codec');
      }
    }

    function initMSEPath() {
      mediaSource = new MSEClass();
      videoEl.src = URL.createObjectURL(mediaSource);

      mediaSource.addEventListener('sourceopen', async () => {
        if (destroyed) return;
        try {
          sourceBuffer = mediaSource.addSourceBuffer(mseType);
        } catch (e) {
          opts.onError?.('MSE addSourceBuffer failed: ' + e.message);
          return;
        }
        sourceBuffer.addEventListener('updateend', () => {
          sbUpdating = false;
          feedNext();
        });
        await startDemux();
      });
    }

    function feedNext() {
      if (destroyed || !sourceBuffer || sbUpdating || pendingSegments.length === 0) return;
      sbUpdating = true;
      try {
        sourceBuffer.appendBuffer(pendingSegments.shift());
      } catch (e) {
        sbUpdating = false;
        if (e.name === 'QuotaExceededError' && sourceBuffer.buffered.length > 0) {
          const ct = videoEl.currentTime;
          try { sourceBuffer.remove(0, Math.max(0, ct - 60)); } catch (_) {}
        }
      }
    }

    async function startDemux() {
      fetchController = new AbortController();
      try {
        const response = await fetch(streamUrl, { signal: fetchController.signal, credentials: 'include' });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const arrayBuf = await response.arrayBuffer();
        if (destroyed) return;

        // Use mediabunny to demux the container into raw tracks
        // then remux to fMP4 for MSE
        if (MB.demux) {
          const demuxed = await MB.demux({ data: new Uint8Array(arrayBuf) });
          if (destroyed) return;
          // Remux to fMP4
          if (MB.remux) {
            const fmp4 = await MB.remux({ data: demuxed, format: 'mp4' });
            if (destroyed) return;
            pendingSegments.push(fmp4.data.buffer || fmp4.data);
            feedNext();
            // Signal end of stream after buffer flushes
            const waitEnd = () => {
              if (pendingSegments.length === 0 && !sbUpdating) {
                try { mediaSource.endOfStream(); } catch (_) {}
              } else { setTimeout(waitEnd, 100); }
            };
            setTimeout(waitEnd, 200);
          }
        }
      } catch (e) {
        if (!destroyed && e.name !== 'AbortError') {
          opts.onError?.('mediabunny demux error: ' + e.message);
        }
      }
    }

    function initWebCodecsPath() {
      // WebCodecs fallback for codecs MSE can't handle
      // This creates a canvas-based renderer
      opts.onError?.('WebCodecs canvas rendering not yet implemented — falling back');
    }

    function seekTo(time) {
      if (!destroyed) videoEl.currentTime = time;
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      try { fetchController?.abort(); } catch (_) {}
      try {
        if (mediaSource && mediaSource.readyState === 'open') mediaSource.endOfStream();
      } catch (_) {}
      reader = null;
      mediaSource = null;
      sourceBuffer = null;
      pendingSegments = [];
    }

    init();
    return { seekTo, destroy, type: 'mse-mediabunny' };
  }

  // ════════════════════════════════════════════
  //  Public API
  // ════════════════════════════════════════════


  // ------------------------------------------------------------------
  // SECTION: Public API
  // Start, availability, label and diagnostics.
  // ------------------------------------------------------------------
  /**
   * Start a smart streaming pipeline for the given video element.
   *
   * @param {HTMLVideoElement} videoEl - The <video> element
   * @param {string} streamUrl - The /stream/{cid}/{mid} byte-range URL
   * @param {object} info - The /api/play/info result
   * @param {object} opts - { onError, onProgress, onComplete, startTime }
   * @returns {object|null} Pipeline handle { seekTo, destroy, type } or null if no pipeline fits
   */
  function startSmartStream(videoEl, streamUrl, info, opts = {}) {
    const pipeline = chooseSmartPipeline(info);
    if (!pipeline) return null;

    switch (pipeline) {
      case 'mse-mp4box':
        return createMP4BoxMSEPipeline(videoEl, streamUrl, info, opts);
      case 'mse-mpegts':
        return createMpegtsMSEPipeline(videoEl, streamUrl, info, opts);
      case 'mse-mediabunny':
        return createMediaBunnyPipeline(videoEl, streamUrl, info, opts);
      case 'webcodecs':
        // WebCodecs canvas pipeline — future enhancement
        return null;
      default:
        return null;
    }
  }

  /**
   * Check if smart streaming is available for this media.
   */
  function canSmartStream(info) {
    return chooseSmartPipeline(info) !== null;
  }

  /**
   * Get a human-readable label for the smart pipeline that would be used.
   */
  function smartStreamLabel(info) {
    const p = chooseSmartPipeline(info);
    if (!p) return '';
    const labels = {
      'mse-mp4box':      'Client-side MSE (MP4)',
      'mse-mpegts':      'Client-side MSE (MPEG-TS)',
      'mse-mediabunny':  'Client-side MSE (remux)',
      'webcodecs':       'WebCodecs decode',
    };
    return labels[p] || p;
  }

  /**
   * Report supported capabilities to the console for diagnostics.
   */
  function diagnostics() {
    return {
      mse:         hasMSE(),
      managedMSE:  hasManagedMSE(),
      webcodecs:   hasWebCodecs(),
      mp4box:      hasMP4Box(),
      mpegts:      hasMpegts(),
      mediabunny:  hasMediaBunny(),
      codecs: {
        'h264+aac':   canMSEPlay('h264', 'aac'),
        'hevc+aac':   canMSEPlay('hevc', 'aac'),
        'vp9+opus':   canMSEPlay('vp9', 'opus'),
        'av1+opus':   canMSEPlay('av1', 'opus'),
        'av1+aac':    canMSEPlay('av1', 'aac'),
      },
    };
  }

  // ── Export ──
  global.ArchiveXSmartStream = Object.freeze({
    start:       startSmartStream,
    canPlay:     canSmartStream,
    label:       smartStreamLabel,
    diagnostics: diagnostics,
    choosePipeline: chooseSmartPipeline,
    mseMimeFor:  mseMimeFor,
    canMSEPlay:  canMSEPlay,
  });

})(window);
