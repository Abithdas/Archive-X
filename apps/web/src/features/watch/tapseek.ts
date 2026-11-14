/** YouTube-style double-tap skipping: a second tap on the left/right third skips 10 s, and every further tap in quick succession adds another 10 s. */
const SKIP_STEP = 10
export const DOUBLE_TAP_MS = 280
export const KEEP_SKIPPING_MS = 700

export interface TapState { seeking: boolean; side: 'left' | 'right' | 'mid'; total: number; at: number }
export interface TapResult { state: TapState; /** seconds to skip now (negative = back), or null when this tap does not skip */ skip: number | null }

export const sideOf = (x: number): TapState['side'] => (x < 0.33 ? 'left' : x > 0.67 ? 'right' : 'mid')

export function registerTap(prev: TapState | null, x: number, now: number): TapResult {
  const side = sideOf(x)
  if (side !== 'mid' && prev) {
    if (prev.seeking && now - prev.at < KEEP_SKIPPING_MS) {
      const total = side === prev.side ? prev.total + SKIP_STEP : SKIP_STEP
      return { state: { seeking: true, side, total, at: now }, skip: side === 'left' ? -SKIP_STEP : SKIP_STEP }
    }
    if (!prev.seeking && now - prev.at < DOUBLE_TAP_MS) {
      return { state: { seeking: true, side, total: SKIP_STEP, at: now }, skip: side === 'left' ? -SKIP_STEP : SKIP_STEP }
    }
  }
  return { state: { seeking: false, side, total: 0, at: now }, skip: null }
}
