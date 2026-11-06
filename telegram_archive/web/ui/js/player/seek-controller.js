/* ============================================================================
 * FILE: js/player/seek-controller.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Playback seek and scrub coordination.
 *
 * Live frame previews are advisory and remain separate from the native media
 * clock. This controller owns the one committed seek on release.
 */
(function registerSeekController(root) {
  'use strict';

  function createSeekController(options = {}) {
    const getState = options.getState || (() => ({}));
    const getPlayer = options.getPlayer || (() => null);
    const getDuration = options.getDuration || (() => 0);
    const getCurrent = options.getCurrent || (() => 0);
    const getPreview = options.getPreview || (() => null);
    const isPreviewEnabled = options.isPreviewEnabled || (() => false);
    const dragTo = options.dragTo || (() => {});
    const showUI = options.showUI || (() => {});
    const updateUI = options.updateUI || (() => {});
    const seekPart = options.seekPart || (() => false);
    const startPlayback = options.startPlayback || (() => {});
    const getInitialMode = options.getInitialMode || (() => 'direct');
    const getCurrentVideo = options.getCurrentVideo || (() => null);

    function beginScrub() {
      const state = getState(), player = getPlayer(), duration = getDuration();
      if (!state.cid || !state.mediaKey || !player?.getAttribute?.('src') || !duration) return;
      if (state.scrubSession) {
        root.clearTimeout(state.scrubSession.timer);
        return;
      }
      // Pointer movement stays visual-only. The native player clock is updated
      // once, on release, so previews do not trigger competing range requests.
      state.scrubSession = {
        key: state.mediaKey,
        seq: state.seq,
        resume: !player.paused,
        start: getCurrent(),
        target: getCurrent(),
        lastAt: 0,
        timer: 0
      };
    }

    function moveScrub(time) {
      const duration = getDuration();
      if (!duration) return;
      const target = Math.max(0, Math.min(Math.max(0, duration - 0.5), Number(time) || 0));
      dragTo(target / duration);
      const session = getState().scrubSession;
      if (!session) return;
      session.target = target;
      if (isPreviewEnabled()) getPreview()?.preview?.(target / duration, false, true);
    }

    function finishScrub(target = null, preserveLivePreview = false) {
      const state = getState(), session = state.scrubSession;
      if (!session) return;
      root.clearTimeout(session.timer);
      if (target !== null && Number.isFinite(Number(target))) {
        session.target = Math.max(0, Math.min(Math.max(0, getDuration() - 0.5), Number(target) || 0));
      }
      const finalTarget = session.target;
      const key = session.key;
      const sequence = session.seq;
      const resume = !!session.resume;
      state.scrubSession = null;
      if (key !== state.mediaKey || sequence !== state.seq) return;
      getPreview()?.retainFrameOverPlayer?.(finalTarget);
      if (resume) state.resumeAfterScrub = { key, seq: sequence, used: false };
      seek(finalTarget, !!preserveLivePreview);
      if (resume) {
        const resumeVideo = () => {
          const pending = state.resumeAfterScrub;
          const player = getPlayer();
          if (!pending || pending.used || pending.key !== state.mediaKey || pending.seq !== state.seq) return;
          pending.used = true;
          state.resumeAfterScrub = null;
          if (!player?.error && !player?.seeking && player?.readyState >= 2) player.play().catch(() => {});
        };
        root.setTimeout(resumeVideo, 850);
      }
    }

    function seek(time, preserveLivePreview = false, quiet = false) {
      const state = getState(), player = getPlayer(), duration = getDuration();
      if (!player) return;
      const target = Math.max(0, duration ? Math.min(Number(time) || 0, duration - 0.5) : Math.max(0, Number(time) || 0));
      if (state.parts && state.partOffsets && seekPart(target)) return;
      if (!player.getAttribute('src')) {
        if (getCurrentVideo() && state.cid && state.mid) {
          startPlayback(getInitialMode(state.info || {}), target);
          if (!quiet) showUI();
        }
        return;
      }
      const sequence = ++state.seekSeq;
      state.seekCount++;
      const preview = getPreview();
      if (!preserveLivePreview) preview?.cancelForSeek?.();
      if (state.mode === 'direct') {
        let buffered = false;
        for (let index = 0; index < player.buffered.length; index++) {
          if (target >= player.buffered.start(index) && target <= player.buffered.end(index) - 0.35) {
            buffered = true;
            break;
          }
        }
        if (!buffered && state.cid && state.mid) {
          // Native seeking emits the exact HTTP Range request; do not race it
          // with a predictive warm request.
          try { player.currentTime = target; } catch {}
          if (!quiet) showUI();
          return;
        }
        try { player.currentTime = target; } catch {}
        if (sequence === state.seekSeq) {
          updateUI();
          if (!quiet) showUI();
        }
        return;
      }
      if (state.mode === 'compat') {
        const relativeTarget = target - state.off;
        let buffered = false;
        for (let index = 0; index < player.buffered.length; index++) {
          if (relativeTarget >= player.buffered.start(index) && relativeTarget <= player.buffered.end(index) - 0.5) {
            buffered = true;
            break;
          }
        }
        if (buffered) player.currentTime = relativeTarget;
        else startPlayback(state.mode, target);
      } else {
        player.currentTime = target;
      }
      updateUI();
      if (!quiet) showUI();
    }

    return Object.freeze({
      pLiveScrubBegin: beginScrub,
      pLiveScrubTo: moveScrub,
      pLiveScrubFinish: finishScrub,
      pSeek: seek
    });
  }

  root.ArchiveXPlayerModules = root.ArchiveXPlayerModules || {};
  root.ArchiveXPlayerModules.createSeekController = createSeekController;
})(window);
