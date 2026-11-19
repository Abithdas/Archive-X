import { describe, expect, it } from 'vitest'
import { statRows } from './streamStats'
import { DEFAULT_PS, isDefaultPS, normalizePS, pictureFilter } from './pictureSound'

describe('stream stats', () => {
  it('shows loaded and the way it plays before any sample', () => {
    expect(statRows(40, null).map((r) => r.k)).toEqual(['Buffered', 'Ahead', 'Playback'])
  })
  it('adds picture size, dropped frames and speed once the video reports them', () => {
    const rows = statRows(100, { width: 1920, height: 1080, ahead: 12.34, dropped: 0, total: 900, rate: 1.5 })
    expect(rows.find((r) => r.k === 'Picture')?.v).toBe('1920×1080')
    expect(rows.find((r) => r.k === 'Ahead')?.v).toBe('12.3 s')
    expect(rows.find((r) => r.k === 'Dropped frames')).toMatchObject({ v: '0 of 900', good: true })
    expect(rows.find((r) => r.k === 'Speed')?.v).toBe('1.5x')
  })
})
describe('picture and sound', () => {
  it('has no filter when untouched and clamps stored values', () => {
    expect(pictureFilter(DEFAULT_PS)).toBeUndefined()
    expect(pictureFilter({ ...DEFAULT_PS, contrast: 120 })).toBe('brightness(100%) contrast(120%) saturate(100%)')
    expect(normalizePS({ brightness: 999, boost: 10, saturation: Number.NaN })).toEqual({ ...DEFAULT_PS, brightness: 150, boost: 100 })
    expect(isDefaultPS(normalizePS(null))).toBe(true)
  })
})
