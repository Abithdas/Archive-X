import { afterEach, describe, expect, it, vi } from 'vitest'
import { installGlow } from './glow'

afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('card glow', () => {
  it('tells the card under the pointer where the pointer is', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    vi.stubGlobal('requestAnimationFrame', (cb: () => void) => { cb(); return 1 })
    document.body.innerHTML = '<div data-glow id="c"><span id="inner"></span></div>'
    const card = document.getElementById('c')!
    card.getBoundingClientRect = () => ({ left: 10, top: 20, width: 200, height: 100, right: 210, bottom: 120, x: 10, y: 20, toJSON() {} })
    Object.defineProperty(card, 'offsetWidth', { value: 200 })
    const off = installGlow()
    const ev = new MouseEvent('pointermove', { clientX: 60, clientY: 70, bubbles: true }); Object.defineProperty(ev, 'pointerType', { value: 'mouse' })
    document.getElementById('inner')!.dispatchEvent(ev)
    expect(card.style.getPropertyValue('--gx')).toBe('50px'); expect(card.style.getPropertyValue('--gy')).toBe('50px')
    off()
  })
  it('does nothing on a touch-only device', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    const add = vi.spyOn(document, 'addEventListener'); installGlow()
    expect(add).not.toHaveBeenCalledWith('pointermove', expect.anything(), expect.anything()); add.mockRestore()
  })
})
