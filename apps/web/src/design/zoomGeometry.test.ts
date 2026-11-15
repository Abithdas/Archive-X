import { describe, expect, it } from 'vitest'
import { flight, frames, targetBox } from './zoomGeometry'

describe('targetBox', () => {
  it('opens half the window wide, centred, in the picture\'s own shape', () => {
    expect(targetBox(1600, 1000, 16 / 9)).toEqual({ left: 400, top: 275, width: 800, height: 450 })
  })
  it('never taller than 82% of the window, and never wider than the window', () => {
    const tall = targetBox(1600, 800, 0.5)
    expect(tall.height).toBe(656); expect(tall.width).toBe(328)
    expect(targetBox(400, 900, 1).width).toBe(360)               // at least 360 wide so it is worth opening…
    expect(targetBox(300, 900, 1).width).toBe(268)               // …but always inside the window, with a margin
    expect(targetBox(1000, 1000, Number.NaN).width).toBe(500)   // an unknown shape falls back to 16:9
  })
})

describe('flight', () => {
  const to = { left: 400, top: 275, width: 800, height: 450 }
  it('scales evenly from the thumbnail and clips the part the thumbnail did not show', () => {
    const f = flight({ left: 100, top: 100, width: 96, height: 56 }, to, 1600, 1000)
    expect(f.scale).toBeCloseTo(0.12, 2)
    expect(f.clip[1]).toBeGreaterThan(0)                         // 96:56 is narrower than 16:9, so the sides are cut
    expect(f.dx).toBeCloseTo(148 - 800, 0); expect(f.dy).toBeCloseTo(128 - 500, 0)
  })
  it('leans away from the centre: a thumbnail on the left tilts one way, on the right the other, and in the middle it stays flat', () => {
    const left = flight({ left: 100, top: 480, width: 96, height: 56 }, to, 1600, 1000)
    const right = flight({ left: 1300, top: 480, width: 96, height: 56 }, to, 1600, 1000)
    const middle = flight({ left: 752, top: 497, width: 96, height: 56 }, to, 1600, 1000)
    expect(Math.sign(left.tiltY)).toBe(-Math.sign(right.tiltY)); expect(Math.abs(left.tiltY)).toBeGreaterThan(5)
    expect(Math.abs(middle.tiltY)).toBeLessThan(1); expect(Math.abs(left.tiltY)).toBeLessThanOrEqual(14)
  })
})

describe('frames', () => {
  it('goes from the thumbnail, through a tilted middle, to flat and full size', () => {
    const f = flight({ left: 100, top: 100, width: 96, height: 56 }, { left: 400, top: 275, width: 800, height: 450 }, 1600, 1000)
    const k = frames(f)
    expect(k.map((x) => x.offset)).toEqual([0, 0.45, 1])
    expect(k[0]!.transform).toMatch(/rotateX\(0\.00deg\) rotateY\(0\.00deg\)/)
    expect(k[1]!.transform).not.toMatch(/rotateY\(0\.00deg\)/)
    expect(k[2]!.transform).toBe('translate(0.00px, 0.00px) scale(1.0000) rotateX(0.00deg) rotateY(0.00deg)')
    expect(k[2]!.clipPath).toBe('inset(0.00px 0.00px 0.00px 0.00px round 14.00px)')
  })
})
