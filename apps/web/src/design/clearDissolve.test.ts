import { describe, expect, it, vi } from 'vitest'
import { startClear } from './clearDissolve'

/** jsdom has no Web Animations: a stand-in whose animations end when the test says so. */
function fakeAnimate() {
  const live: Array<{ el: HTMLElement; frames: unknown; opts: Record<string, unknown>; end: () => void; cancelled: boolean }> = []
  const animate = function (this: HTMLElement, frames: unknown, opts: Record<string, unknown>) {
    let end!: () => void
    const finished = new Promise<void>((r) => { end = r })
    const rec = { el: this, frames, opts, end, cancelled: false }
    live.push(rec)
    return { finished, cancel: () => { rec.cancelled = true; end() } } as unknown as Animation
  }
  return { live, animate }
}
const parts = () => ({ ghost: document.createElement('div'), placeholder: document.createElement('div'), glow: document.createElement('div') })

describe('startClear', () => {
  it('sinks the typed text, drops the placeholder in after it and sweeps the light, then tidies up when they have all ended', async () => {
    const f = fakeAnimate()
    const p = parts()
    for (const el of Object.values(p)) (el as unknown as { animate: unknown }).animate = f.animate
    const done = vi.fn()
    startClear(p, 'hello world', { onDone: done })
    expect(p.ghost.textContent).toBe('hello world')
    expect(f.live).toHaveLength(3)
    const [out, back, sweep] = f.live
    expect(out!.el).toBe(p.ghost)
    expect(back!.el).toBe(p.placeholder)
    expect(back!.opts.delay).toBeGreaterThan(0)                    // the placeholder waits for the text to start leaving
    expect(sweep!.el).toBe(p.glow)
    expect(done).not.toHaveBeenCalled()
    f.live.forEach((a) => a.end())
    await Promise.resolve(); await Promise.resolve()
    expect(done).toHaveBeenCalledTimes(1)
    expect(p.ghost.textContent).toBe('')
  })
  it('can be stopped early: everything is cancelled and tidied, and onDone runs once', async () => {
    const f = fakeAnimate()
    const p = parts()
    for (const el of Object.values(p)) (el as unknown as { animate: unknown }).animate = f.animate
    const done = vi.fn()
    const stop = startClear(p, 'abc', { onDone: done })
    stop()
    stop()
    await Promise.resolve(); await Promise.resolve()
    expect(done).toHaveBeenCalledTimes(1)
    expect(f.live.every((a) => a.cancelled)).toBe(true)
    expect(p.ghost.textContent).toBe('')
  })
  it('without animations it just tidies up at once', () => {
    const p = parts()
    const done = vi.fn()
    startClear(p, 'abc', { onDone: done })
    expect(done).toHaveBeenCalledTimes(1)
    expect(p.ghost.textContent).toBe('')
  })
})
