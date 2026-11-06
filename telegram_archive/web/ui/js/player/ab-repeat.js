/* ============================================================================
 * FILE: js/player/ab-repeat.js
 * PURPOSE: A-B repeat loop controller for the player.
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
(function (root) {
  'use strict';

  const MIN_LOOP_SECONDS = 0.25;
  const END_TOLERANCE_SECONDS = 0.04;

  function createABRepeat(options = {}) {
    let start = null;
    let end = null;
    let mediaKey = '';
    let seekPending = false;

    const readKey = () => String(options.getKey?.() ?? '');
    const snapshot = () => ({
      start,
      end,
      mediaKey,
      active: start !== null && end !== null
    });
    const notify = () => options.onChange?.(snapshot());

    function syncKey() {
      const nextKey = readKey();
      if (nextKey === mediaKey) return false;
      const hadRange = start !== null || end !== null;
      mediaKey = nextKey;
      start = null;
      end = null;
      seekPending = false;
      if (hadRange) notify();
      return true;
    }

    function currentContext() {
      syncKey();
      const duration = Number(options.getDuration?.());
      const current = Number(options.getCurrent?.());
      if (!mediaKey || !Number.isFinite(duration) || duration <= 0 || !Number.isFinite(current)) {
        return null;
      }
      return { duration, current };
    }

    function setStart() {
      const context = currentContext();
      if (!context) return { ok: false, reason: 'unavailable' };
      if (context.current < 0 || context.current >= context.duration - MIN_LOOP_SECONDS) {
        return { ok: false, reason: 'start-out-of-range' };
      }
      start = context.current;
      end = null;
      seekPending = false;
      notify();
      return { ok: true, start };
    }

    function setEnd() {
      const context = currentContext();
      if (!context) return { ok: false, reason: 'unavailable' };
      if (start === null) return { ok: false, reason: 'start-not-set' };
      if (context.current < start + MIN_LOOP_SECONDS) {
        return { ok: false, reason: 'end-not-after-start' };
      }
      if (context.current > context.duration + END_TOLERANCE_SECONDS) {
        return { ok: false, reason: 'end-out-of-range' };
      }
      end = Math.min(context.current, context.duration);
      seekPending = false;
      notify();
      return { ok: true, start, end };
    }

    function clear() {
      start = null;
      end = null;
      mediaKey = readKey();
      seekPending = false;
      notify();
      return snapshot();
    }

    function reset(nextKey = '') {
      start = null;
      end = null;
      mediaKey = String(nextKey || '');
      seekPending = false;
      notify();
      return snapshot();
    }

    function onTimeUpdate() {
      syncKey();
      if (start === null || end === null || seekPending) return false;
      if (options.isPaused?.() || options.isSeeking?.()) return false;
      const current = Number(options.getCurrent?.());
      if (!Number.isFinite(current) || current < end - END_TOLERANCE_SECONDS) return false;
      seekPending = true;
      try {
        options.seekTo?.(start);
        return true;
      } catch (_) {
        seekPending = false;
        return false;
      }
    }

    function onSeeked() {
      if (!seekPending) return;
      seekPending = false;
    }

    return { setStart, setEnd, clear, reset, syncKey, snapshot, onTimeUpdate, onSeeked };
  }

  root.ArchiveXPlayerModules = root.ArchiveXPlayerModules || {};
  root.ArchiveXPlayerModules.createABRepeat = createABRepeat;
})(window);
