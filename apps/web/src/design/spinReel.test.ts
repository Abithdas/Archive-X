import { afterEach, describe, expect, it, vi } from 'vitest'
import { rollReel } from './spinReel'

afterEach(() => vi.unstubAllGlobals())

describe('rollReel', () => {
  it('calls onDone straight away when motion is reduced (nothing to wait for)', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    const el = document.createElement('span')
    const done = vi.fn()
    rollReel(el, 60, 7, done)
    expect(done).toHaveBeenCalledTimes(1)
    expect(el.getAttribute('aria-label')).toBe('7')
  })
  it('does not call onDone when it is cancelled before the drums land', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    vi.stubGlobal('requestAnimationFrame', () => 1)
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
    const el = document.createElement('span')
    document.body.appendChild(el)
    const done = vi.fn()
    const stop = rollReel(el, 60, 7, done)
    stop()
    expect(done).not.toHaveBeenCalled()
  })
})
