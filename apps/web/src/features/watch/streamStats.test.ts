import { describe, expect, it } from 'vitest'
import { statRows } from './streamStats'
import { DEFAULT_PS, isDefaultPS, normalizePS, pictureFilter, zoomScale } from './pictureSound'

describe('stream stats', () => {
  it('shows the playback part before anything is known', () => {
    expect(statRows(40, null).map((r) => r.head ?? r.k)).toEqual(['Playback', 'Buffered', 'Ahead', 'Mode'])
  })
  it('adds Video, Audio, dropped frames, speed and the source once they are known', () => {
    const rows = statRows(100, { width: 1920, height: 800, ahead: 12.34, dropped: 0, total: 900, rate: 1.5 }, { vcodec: 'hevc', tenBit: false, container: 'Matroska', bitrate: 2_100_000, acodec: 'aac', audio: 'English', source: 'Telegram' })
    const v = (k: string) => rows.find((r) => r.k === k)?.v
    expect(rows.filter((r) => r.head).map((r) => r.head)).toEqual(['Video', 'Audio', 'Playback'])
    expect(v('Resolution')).toBe('1920 × 800'); expect(v('Codec')).toBe('HEVC · 8-bit'); expect(v('Bitrate')).toBe('2.1 Mb/s'); expect(v('Container')).toBe('Matroska')
    expect(v('English')).toBe('AAC'); expect(v('Ahead')).toBe('12.3 s'); expect(v('Dropped frames')).toBe('0 of 900'); expect(v('Speed')).toBe('1.5x'); expect(v('Source')).toBe('Telegram')
  })
})
describe('picture and sound', () => {
  it('has no filter when untouched, draws sharpen and colour when asked, and clamps stored values', () => {
    expect(pictureFilter(DEFAULT_PS)).toBeUndefined()
    expect(pictureFilter({ ...DEFAULT_PS, enhance: 'sharpen' })).toBe('url(#ax-sharpen)')
    expect(pictureFilter({ ...DEFAULT_PS, enhance: 'vivid' })).toBe('contrast(1.06) saturate(1.2)')
    expect(pictureFilter({ ...DEFAULT_PS, enhance: 'both' })).toBe('url(#ax-sharpen) contrast(1.06) saturate(1.2)')
    expect(normalizePS({ boost: 10, enhance: 'nonsense' as never, brightness: 130 } as never)).toEqual(DEFAULT_PS)   // the old brightness, contrast and colour are gone
    expect(isDefaultPS(normalizePS(null))).toBe(true)
  })
  it('keeps zoom and sound to the known choices and works out the zoom', () => {
    expect(normalizePS({ zoom: '999' as never, sound: 'night' })).toEqual({ ...DEFAULT_PS, sound: 'night' })
    expect(zoomScale('off', 2.4, 1.78)).toBe(1); expect(zoomScale('125', 1, 1)).toBe(1.25)
    expect(zoomScale('fill', 2.4, 16 / 9)).toBeCloseTo(2.4 / (16 / 9)); expect(zoomScale('fill', 16 / 9, 16 / 9)).toBe(1)
  })
})
