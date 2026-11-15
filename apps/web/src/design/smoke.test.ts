import { afterEach, describe, expect, it, vi } from 'vitest'
import { SMOKE_MS, puffs, smokeKey, smokeOut } from './smoke'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

describe('puffs', () => {
  it('makes the asked number, inside and above the card, drifting upward', () => {
    let n = 0
    const rnd = () => ((n = (n * 7 + 3) % 11) / 11)
    const list = puffs(300, 200, 10, rnd)
    expect(list).toHaveLength(10)
    for (const p of list) {
      expect(p.x).toBeGreaterThanOrEqual(0); expect(p.x).toBeLessThanOrEqual(300)
      expect(p.y).toBeGreaterThanOrEqual(70); expect(p.y).toBeLessThanOrEqual(200)   // from the lower part of the card
      expect(p.dy).toBeLessThan(0); expect(p.size).toBeGreaterThanOrEqual(36); expect(p.dur).toBeGreaterThan(0)
    }
    expect(list.map((p) => p.delay)).toEqual([...list.map((p) => p.delay)].sort((a, b) => a - b).length ? list.map((p) => p.delay) : [])
  })
})

describe('smokeOut', () => {
  const card = (w = 240, h = 160) => {
    const el = document.createElement('div')
    el.setAttribute('data-smoke', 'card:-1:7')
    el.getBoundingClientRect = () => ({ left: 10, top: 20, width: w, height: h, right: 10 + w, bottom: 20 + h, x: 10, y: 20, toJSON() {} })
    document.body.appendChild(el)
    return el
  }
  it('does nothing for a missing element, and just hides one with no size (a page that is not showing)', async () => {
    await expect(smokeOut(null)).resolves.toBeUndefined()
    const el = card(0, 0)
    await smokeOut(el)
    expect(el.style.opacity).toBe('0'); expect(document.querySelector('.ax-smoke-layer')).toBeNull()
  })
  it('displaces and fades the card while smoke rises, ends invisible, and cleans up the filter and the smoke afterwards', async () => {
    vi.useFakeTimers()
    let now = 0
    vi.stubGlobal('performance', { now: () => now })
    vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => setTimeout(() => { now += 16; cb(now) }, 16) as unknown as number)
    const el = card()
    const done = smokeOut(el)
    await vi.advanceTimersByTimeAsync(SMOKE_MS / 2)
    expect(el.style.filter).toMatch(/^url\(#ax-smoke-\d+\)$/)
    expect(Number(el.style.opacity)).toBeLessThan(1); expect(Number(el.style.opacity)).toBeGreaterThan(0)
    const displace = document.querySelector('feDisplacementMap')!
    expect(Number(displace.getAttribute('scale'))).toBeGreaterThan(5)
    expect(document.querySelectorAll('.ax-puff').length).toBe(12)
    await vi.advanceTimersByTimeAsync(SMOKE_MS)
    await done
    expect(el.style.opacity).toBe('0')
    await vi.advanceTimersByTimeAsync(800)
    expect(document.querySelector('.ax-smoke-layer')).toBeNull(); expect(document.querySelector('feDisplacementMap')).toBeNull()
  })
  it('finds the element by its data-smoke key', async () => {
    const el = card(0, 0)
    await smokeKey('card:-1:7')
    expect(el.style.opacity).toBe('0')
    await expect(smokeKey('card:none')).resolves.toBeUndefined()
  })
})
