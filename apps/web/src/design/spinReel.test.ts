import { afterEach, describe, expect, it, vi } from 'vitest'
import { rollReel, showReel } from './spinReel'
import { setMotion } from './motionPref'

afterEach(() => { vi.unstubAllGlobals(); setMotion('full') })

describe('rollReel', () => {
  it('calls onDone straight away when the person follows the system and it asks for reduced motion', () => {
    setMotion('system')
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    const el = document.createElement('span')
    const done = vi.fn()
    rollReel(el, 60, 7, done)
    expect(done).toHaveBeenCalledTimes(1)
    expect(el.getAttribute('aria-label')).toBe('7')
  })
  it('does not call onDone when it is cancelled before the drums land', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    ;(HTMLElement.prototype as unknown as { animate: unknown }).animate = () => ({ finished: new Promise<void>(() => undefined), cancel: () => undefined })
    try {
      const el = document.createElement('span')
      document.body.appendChild(el)
      const done = vi.fn()
      const stop = rollReel(el, 60, 7, done)
      stop()
      expect(done).not.toHaveBeenCalled()
    } finally { delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate }
  })
  it('rests each digit on its cell exactly: the strip moves in em, never in a pixel count rounded from the column', () => {
    const el = document.createElement('span')
    document.body.appendChild(el)
    // a column whose height is a whole number of pixels (jsdom has no layout) must not matter: 15.6 px cells were once measured as 16 and the 7 and 9 sat 3 px high
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 16 })
    try {
      showReel(el, 1790)
      const moves = [...el.querySelectorAll<HTMLElement>('.mo-reel-strip')].map((s) => s.style.transform)
      expect(moves).toEqual(['translateY(-1.200em)', 'translateY(-8.400em)', 'translateY(-10.800em)', 'translateY(0.000em)'])
    } finally { delete (HTMLElement.prototype as unknown as { offsetHeight?: unknown }).offsetHeight }
  })
})
