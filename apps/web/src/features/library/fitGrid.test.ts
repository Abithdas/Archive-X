import { describe, expect, it } from 'vitest'
import { columnsFor, fitPer } from './fitGrid'

describe('columns that fit', () => {
  it('counts whole cards with the gaps between them', () => {
    expect(columnsFor(1000, 270, 16)).toBe(3)       // 3 x 270 + 2 x 16 = 842; a 4th would need 1128
    expect(columnsFor(1128, 270, 16)).toBe(4)
    expect(columnsFor(100, 270, 16)).toBe(1)
    expect(columnsFor(0, 270, 16)).toBe(1)
  })
})

describe('page size in whole rows', () => {
  it('rounds up to a full row, so the last row is never half empty', () => {
    expect(fitPer(12, 5)).toBe(15)
    expect(fitPer(24, 5)).toBe(25)
    expect(fitPer(48, 5)).toBe(50)
    expect(fitPer(12, 4)).toBe(12)
    expect(fitPer(12, 7)).toBe(14)
  })
  it('is at least one row, also for a one-column phone', () => {
    expect(fitPer(12, 1)).toBe(12)
    expect(fitPer(3, 6)).toBe(6)
    expect(fitPer(0, 3)).toBe(3)
  })
})
