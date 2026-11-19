import { describe, expect, it } from 'vitest'
import { PERFORMER_CARD, VIDEO_CARD, columnsForScreen } from './screenGrid'

const rowsOf = (n: number, c: number) => Math.ceil(n / c)

describe('columns that fit the chosen number of cards on the screen', () => {
  it('always gives at least two rows', () => {
    for (const n of [4, 12, 24, 48, 96]) expect(rowsOf(n, columnsForScreen(n, 1400, 5000, 16, VIDEO_CARD))).toBeGreaterThanOrEqual(2)
  })
  it('makes the cards smaller as the number grows', () => {
    const w = (n: number) => (1400 - 16 * (columnsForScreen(n, 1400, 800, 16, VIDEO_CARD) - 1)) / columnsForScreen(n, 1400, 800, 16, VIDEO_CARD)
    expect(w(12)).toBeGreaterThan(w(24)); expect(w(24)).toBeGreaterThan(w(48)); expect(w(48)).toBeGreaterThanOrEqual(w(96))
  })
  it('fits the height when it can: 12 video cards in 1400 x 800', () => {
    const c = columnsForScreen(12, 1400, 800, 16, VIDEO_CARD)
    const w = (1400 - 16 * (c - 1)) / c
    expect(rowsOf(12, c) * (w * VIDEO_CARD.aspect + VIDEO_CARD.extra) + 16 * (rowsOf(12, c) - 1)).toBeLessThanOrEqual(800)
  })
  it('gets smaller when the visible height shrinks (zoomed in), taller posters need more columns', () => {
    expect(columnsForScreen(24, 1400, 400, 16, PERFORMER_CARD)).toBeGreaterThanOrEqual(columnsForScreen(24, 1400, 1200, 16, PERFORMER_CARD))
  })
  it('never makes a card unreadably small, and copes with few cards or no width', () => {
    expect(1400 / columnsForScreen(500, 1400, 300, 16, VIDEO_CARD)).toBeGreaterThanOrEqual(90)
    expect(columnsForScreen(1, 1400, 800, 16, VIDEO_CARD)).toBe(1); expect(columnsForScreen(3, 1400, 800, 16, VIDEO_CARD)).toBeGreaterThanOrEqual(1); expect(columnsForScreen(12, 0, 800, 16, VIDEO_CARD)).toBe(1)
  })
})
