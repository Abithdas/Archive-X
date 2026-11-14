import { describe, expect, it } from 'vitest'
import { registerTap, sideOf, type TapState } from './tapseek'

describe('double-tap skipping', () => {
  it('maps position to a side', () => { expect([sideOf(0.1), sideOf(0.5), sideOf(0.9)]).toEqual(['left', 'mid', 'right']) })
  it('the first tap only arms; the second within 280 ms skips 10 s', () => {
    const a = registerTap(null, 0.9, 1000); expect(a.skip).toBeNull()
    const b = registerTap(a.state, 0.9, 1200); expect(b.skip).toBe(10); expect(b.state.total).toBe(10)
  })
  it('a slow second tap does not skip', () => { expect(registerTap(registerTap(null, 0.9, 0).state, 0.9, 400).skip).toBeNull() })
  it('keeps adding 10 s per tap while tapping quickly, and counts the total', () => {
    let s: TapState | null = null; let last = 0
    const out: number[] = []
    for (const t of [0, 150, 400, 700, 1000]) { const r = registerTap(s, 0.1, t); s = r.state; out.push(r.state.total); last = r.skip ?? last }
    expect(out).toEqual([0, 10, 20, 30, 40]); expect(last).toBe(-10)
  })
  it('stops skipping after 700 ms of no taps, and a middle tap never skips', () => {
    const s = registerTap(registerTap(null, 0.9, 0).state, 0.9, 100).state
    expect(registerTap(s, 0.9, 900).skip).toBeNull()
    expect(registerTap(s, 0.5, 200).skip).toBeNull()
  })
  it('tapping the other side restarts the count the other way', () => {
    const s = registerTap(registerTap(null, 0.9, 0).state, 0.9, 100).state
    const r = registerTap(s, 0.1, 300); expect(r.skip).toBe(-10); expect(r.state.total).toBe(10)
  })
})
