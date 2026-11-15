import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { bezier, glowLayers, startClear } from './clearDissolve'

describe('bezier', () => {
  it('starts at 0, ends at 1 and a linear curve changes nothing', () => {
    const e = bezier('cubic-bezier(0.22, 1, 0.36, 1)')
    expect(e(0)).toBe(0)
    expect(e(1)).toBe(1)
    expect(e(0.5)).toBeGreaterThan(0.5)     // ease-out: more than half done at half time
    const lin = bezier('cubic-bezier(0, 0, 1, 1)')
    expect(lin(0.3)).toBeCloseTo(0.3, 3)
  })
  it('falls back to a straight line for anything it cannot read', () => expect(bezier('ease-in-out')(0.25)).toBe(0.25))
})

describe('glowLayers', () => {
  const measure = (s: string) => s.length * 8
  it('writes four soft ellipses under every word and none for the spaces', () => {
    const css = glowLayers('hello big world', measure, { padLeft: 10, width: 300, spread: 1.5, dark: false })
    expect(css.match(/radial-gradient/g)).toHaveLength(12)
    expect(css).toContain('rgba(0,0,0,0.22)')
  })
  it('paints white streaks on a dark surface', () => expect(glowLayers('hi', measure, { padLeft: 0, width: 100, spread: 1, dark: true })).toContain('rgba(255,255,255,'))
  it('is empty for an empty or all-space text', () => {
    expect(glowLayers('', measure, { padLeft: 0, width: 100, spread: 1, dark: false })).toBe('')
    expect(glowLayers('   ', measure, { padLeft: 0, width: 100, spread: 1, dark: false })).toBe('')
  })
})

describe('startClear', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())
  const parts = () => ({ ghost: document.createElement('div'), placeholder: document.createElement('div'), glow: document.createElement('div') })

  it('flies the text out, fades it, lights the glow and tidies up at the end', () => {
    const p = parts()
    const done = vi.fn()
    startClear(p, 'hello world', { measure: (s) => s.length * 8, padLeft: 10, width: 300, dark: false, onDone: done })
    expect(p.ghost.textContent).toBe('hello world')
    vi.advanceTimersByTime(200)
    expect(parseFloat(p.ghost.style.opacity)).toBeLessThan(1)
    expect(p.ghost.style.transform).toMatch(/translateY\([\d.]+px\)/)
    expect(parseFloat(p.glow.style.opacity)).toBeGreaterThan(0)
    expect(done).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1100)
    expect(done).toHaveBeenCalledTimes(1)
    expect(p.ghost.textContent).toBe('')
    expect(p.glow.style.opacity).toBe('0')
  })
  it('can be stopped early and still leaves everything tidy, once', () => {
    const p = parts()
    const done = vi.fn()
    const stop = startClear(p, 'abc', { measure: (s) => s.length * 8, padLeft: 0, width: 100, dark: true, onDone: done })
    vi.advanceTimersByTime(100)
    stop()
    stop()
    vi.advanceTimersByTime(2000)
    expect(done).toHaveBeenCalledTimes(1)
    expect(p.ghost.textContent).toBe('')
  })
})
