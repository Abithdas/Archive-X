import { describe, expect, it } from 'vitest'
import { aspectOf, assignColumns, columnsFor } from './masonry'

describe('masonry columns', () => {
  it('puts each photo in the shortest column', () => {
    // tall (aspect .5 = height 2), then two wide ones fill the other column before the next goes back
    expect(assignColumns([0.5, 2, 2, 2], 2)).toEqual([[0], [1, 2, 3]])
  })
  it('keeps every earlier photo where it was when more are added', () => {
    const base = [0.75, 1.5, 1, 0.66, 1.33, 0.8, 1.2, 1]
    const before = assignColumns(base, 4)
    const after = assignColumns([...base, 0.9, 1.1, 0.7, 1.6, 1], 4)
    before.forEach((col, c) => expect(after[c]!.slice(0, col.length)).toEqual(col))
  })
  it('covers every photo exactly once', () => {
    const cols = assignColumns(Array.from({ length: 37 }, (_, i) => 0.5 + (i % 5) * 0.3), 6)
    expect(cols.flat().sort((a, b) => a - b)).toEqual(Array.from({ length: 37 }, (_, i) => i))
  })
  it('copes with odd input', () => {
    expect(assignColumns([], 3)).toEqual([[], [], []])
    expect(assignColumns([0, Number.NaN, -1], 2).flat()).toHaveLength(3)
    expect(assignColumns([1, 1], 0)).toEqual([[0, 1]])
  })
  it('measures a photo from its size, defaulting to 4:3', () => {
    expect(aspectOf({ width: 800, height: 1200 })).toBeCloseTo(0.667, 2)
    expect(aspectOf({ width: 0, height: 0 })).toBeCloseTo(1.333, 2)
  })
  it('uses 2, 3, 4 and 6 columns at the old breakpoints', () => {
    expect([320, 639, 640, 1023, 1024, 1279, 1280, 2560].map(columnsFor)).toEqual([2, 2, 3, 3, 4, 4, 6, 6])
  })
})
