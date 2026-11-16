/** Decisions of the native player pane that need no engine: whether playback has gone into slow motion (and a short refill is wanted), and
 *  whether the video window may be shown over the page at this moment. */

// ---------------------------------------------------------------- slow motion after a jump -----------------------------------------------------------
// After a jump the new place has to be fetched from Telegram. Until it arrives the decoder is fed slower than real time and the picture crawls
// (the clock follows the data). A browser player stops and shows "buffering"; this does the same: when the position has advanced at under
// 0.7 of the real speed for about a second after a jump, playback is paused for 2.5 seconds so the buffer fills, then it carries on.
export const SLOW_BELOW = 0.7
export const SLOW_FOR_MS = 1200
export const HOLD_MS = 2500
export const MAX_HOLDS = 3          // per jump: a stream that is slow all the way is not made worse by stopping again and again
export const WATCH_MS = 20_000      // how long after a jump to keep watching
const MIN_SAMPLE_MS = 200

export interface Sample { at: number; position: number }
export interface StallState { last: Sample | null; slowSince: number | null; holdUntil: number | null; used: number; watchUntil: number }
export type StallAction = 'none' | 'pause' | 'resume'

export const quiet = (): StallState => ({ last: null, slowSince: null, holdUntil: null, used: 0, watchUntil: 0 })
/** A jump starts a fresh watch. */
export const afterJump = (now: number): StallState => ({ ...quiet(), watchUntil: now + WATCH_MS })

/** One look at the player (`position` in seconds at time `now` in ms). Returns the next state and what to do about it. */
export function stallStep(s: StallState, now: number, position: number, o: { playing: boolean; rate: number }): { state: StallState; action: StallAction } {
  if (s.holdUntil !== null) {
    if (now < s.holdUntil) return { state: s, action: 'none' }
    return { state: { ...s, holdUntil: null, last: null, slowSince: null }, action: 'resume' }
  }
  if (!o.playing || now > s.watchUntil) return { state: s.last || s.slowSince !== null ? { ...s, last: null, slowSince: null } : s, action: 'none' }
  if (!s.last) return { state: { ...s, last: { at: now, position } }, action: 'none' }
  const dt = now - s.last.at
  if (dt < MIN_SAMPLE_MS) return { state: s, action: 'none' }
  const speed = (position - s.last.position) / (dt / 1000) / Math.max(0.25, o.rate)
  const last = { at: now, position }
  if (speed >= SLOW_BELOW) return { state: { ...s, last, slowSince: null }, action: 'none' }
  const since = s.slowSince ?? now
  if (now - since >= SLOW_FOR_MS && s.used < MAX_HOLDS) return { state: { ...s, last: null, slowSince: null, holdUntil: now + HOLD_MS, used: s.used + 1 }, action: 'pause' }
  return { state: { ...s, last, slowSince: since }, action: 'none' }
}

// ---------------------------------------------------------------- the video window over the page ----------------------------------------------------
// The picture is a separate Windows window above the app, so it cannot be clipped by the page: it is shown only while the whole stage is in view
// (not under the top bar, not scrolled partly out) and nothing (a dialog, a menu) is open over it.
export interface Box { top: number; bottom: number; left: number; right: number }
export function videoVisible(stage: Box, view: Box, covered: boolean): boolean {
  if (covered || stage.bottom <= stage.top || stage.right <= stage.left) return false
  return stage.top >= view.top - 1 && stage.bottom <= view.bottom + 1 && stage.left >= view.left - 1 && stage.right <= view.right + 1
}

/** The stage's width / height: the real picture once it is open, else what the library knows, else 16:9. */
export function aspectOf(opened: { width: number; height: number } | null, hint: { width?: number | null; height?: number | null }): number {
  for (const v of [opened, hint]) {
    const w = Number(v?.width), h = Number(v?.height)
    if (w > 0 && h > 0) return w / h
  }
  return 16 / 9
}
