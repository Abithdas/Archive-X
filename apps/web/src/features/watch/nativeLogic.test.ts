import { describe, expect, it } from 'vitest'
import { HOLD_MS, MAX_HOLDS, SLOW_FOR_MS, WATCH_MS, afterJump, aspectOf, needsNative, quiet, stallStep, startsNative, videoVisible, type StallState } from './nativeLogic'

/** Feed the detector one look every 250 ms; `speed` is how fast the position moves (1 = real time). Returns the actions with their times. */
function run(speed: number, from: StallState, seconds: number, opts: { rate?: number; playing?: boolean } = {}) {
  let s = from, pos = 100, t = 1000
  const out: Array<{ t: number; action: string }> = []
  for (let i = 0; i < seconds * 4; i++) {
    t += 250; pos += 0.25 * speed
    const r = stallStep(s, t, pos, { playing: opts.playing ?? true, rate: opts.rate ?? 1 })
    s = r.state
    if (r.action !== 'none') out.push({ t, action: r.action })
  }
  return { s, out }
}

describe('slow motion after a jump', () => {
  it('does nothing when playback runs at real speed', () => {
    expect(run(1, afterJump(1000), 15).out).toEqual([])
  })
  it('pauses after about a second of crawling, then resumes after the hold', () => {
    const { out } = run(0.3, afterJump(1000), 6)
    expect(out[0]!.action).toBe('pause')
    expect(out[0]!.t - 1000).toBeLessThanOrEqual(SLOW_FOR_MS + 750)
    const resume = out.find((o) => o.action === 'resume')!
    expect(resume.t - out[0]!.t).toBeGreaterThanOrEqual(HOLD_MS)
    expect(resume.t - out[0]!.t).toBeLessThanOrEqual(HOLD_MS + 300)
  })
  it('stops intervening after a few holds in one jump', () => {
    const { out } = run(0.2, afterJump(1000), 19)
    expect(out.filter((o) => o.action === 'pause').length).toBe(MAX_HOLDS)
  })
  it('only watches for a while after a jump, and never when nothing was jumped', () => {
    expect(run(0.2, quiet(), 10).out).toEqual([])
    expect(run(0.2, { ...afterJump(1000), watchUntil: 1000 + WATCH_MS }, 30).out.every((o) => o.t <= 1000 + WATCH_MS + HOLD_MS + 500)).toBe(true)
  })
  it('ignores a paused player and does not count the pause as slowness', () => {
    expect(run(0, afterJump(1000), 10, { playing: false }).out).toEqual([])
  })
  it('judges against the chosen speed: 2x that moves at 2x is fine, 2x that moves at 1x is slow', () => {
    expect(run(2, afterJump(1000), 10, { rate: 2 }).out).toEqual([])
    expect(run(1, afterJump(1000), 6, { rate: 2 }).out[0]?.action).toBe('pause')
  })
  it('a brief hiccup is not a stall', () => {
    let s = afterJump(1000); let t = 1000; let pos = 100
    const actions: string[] = []
    for (let i = 0; i < 40; i++) {
      t += 250; pos += i >= 8 && i < 11 ? 0.05 : 0.25     // three slow looks (0.75 s)
      const r = stallStep(s, t, pos, { playing: true, rate: 1 }); s = r.state; if (r.action !== 'none') actions.push(r.action)
    }
    expect(actions).toEqual([])
  })
  it('a new jump starts a fresh count', () => {
    const first = run(0.2, afterJump(1000), 19)
    expect(first.s.used).toBe(MAX_HOLDS)
    expect(afterJump(50_000).used).toBe(0)
  })
})

describe('when the video window may be shown', () => {
  const view = { top: 64, bottom: 800, left: 0, right: 1200 }
  const at = (top: number, bottom: number) => ({ top, bottom, left: 100, right: 700 })
  it('is shown when the whole stage is in view', () => expect(videoVisible(at(100, 460), view, false)).toBe(true))
  it('is hidden when the stage is under the top bar or below the screen, even partly', () => {
    expect(videoVisible(at(40, 400), view, false)).toBe(false)
    expect(videoVisible(at(500, 860), view, false)).toBe(false)
  })
  it('is hidden while a dialog or menu is open, and for an empty box', () => {
    expect(videoVisible(at(100, 460), view, true)).toBe(false)
    expect(videoVisible({ top: 100, bottom: 100, left: 0, right: 0 }, view, false)).toBe(false)
  })
})

describe('the stage shape', () => {
  it('uses the opened picture, then the library, then 16:9', () => {
    expect(aspectOf({ width: 1920, height: 800 }, { width: 640, height: 480 })).toBeCloseTo(2.4)
    expect(aspectOf(null, { width: 640, height: 480 })).toBeCloseTo(4 / 3)
    expect(aspectOf({ width: 0, height: 0 }, {})).toBeCloseTo(16 / 9)
  })
})

describe('which engine starts', () => {
  const none = () => ''
  const yes = () => 'probably'
  it('never sends a video to the native player on unknown facts, or when the browser plays it', () => {
    expect(needsNative(undefined, none)).toBe(false)
    expect(needsNative({}, none)).toBe(false)
    expect(needsNative({ direct_likely: true }, none)).toBe(false)
  })
  it('sends a file with a container or codec no browser plays', () => {
    expect(needsNative({ direct_likely: false, container_ok: false }, yes)).toBe(true)                       // avi, wmv
    expect(needsNative({ direct_likely: false, container_ok: true, video_ok: false, vcodec: 'mpeg4' }, yes)).toBe(true)
    expect(needsNative({ direct_likely: false, container_ok: true, video_ok: true, audio_ok: false, acodec: 'dts' }, yes)).toBe(true)
  })
  it('trusts the browser\'s own answer for HEVC and Dolby audio', () => {
    const hevc = { direct_likely: false, container_ok: true, video_ok: false, audio_ok: true, vcodec: 'hevc' }
    expect(needsNative(hevc, none)).toBe(true)                      // this browser cannot
    expect(needsNative(hevc, yes)).toBe(false)                      // this browser can (hardware decoding)
    expect(needsNative({ ...hevc, ten_bit: true }, yes)).toBe(true) // 10-bit HEVC: not trusted
    const ac3 = { direct_likely: false, container_ok: true, video_ok: true, audio_ok: false, acodec: 'ac3' }
    expect(needsNative(ac3, none)).toBe(true)
    expect(needsNative(ac3, yes)).toBe(false)
  })
  it('starts by itself only with the engine there, no choice for the browser, and the setting allowing it', () => {
    const base = { nativeOk: true, chosenWeb: false, mode: 'fallback', asked: false, info: { direct_likely: false, container_ok: false }, canPlayType: none }
    expect(startsNative(base)).toBe(true)
    expect(startsNative({ ...base, nativeOk: false })).toBe(false)
    expect(startsNative({ ...base, chosenWeb: true })).toBe(false)
    expect(startsNative({ ...base, mode: 'never' })).toBe(false)
    expect(startsNative({ ...base, info: { direct_likely: true } })).toBe(false)
    expect(startsNative({ ...base, mode: 'always', info: { direct_likely: true } })).toBe(true)
    expect(startsNative({ ...base, mode: 'never', asked: true })).toBe(true)
  })
})
