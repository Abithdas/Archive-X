import { describe, expect, it } from 'vitest'
import { FRACTIONS, framePercent, incomplete, liveSrc, previewQuestion, snapshotIndex, snapshotSrc } from './suggestpreview'

describe('suggestion preview frames', () => {
  it('uses four frames through the video', () => { expect([...FRACTIONS]).toEqual([0.1, 0.35, 0.6, 0.85]) })
  it('maps a fraction onto the 36 stored snapshots', () => {
    expect(FRACTIONS.map(snapshotIndex)).toEqual([3, 12, 21, 30])
    expect(snapshotIndex(0)).toBe(0)
    expect(snapshotIndex(1)).toBe(35)
  })
  it('labels a tile with where that snapshot really is', () => {
    expect(framePercent(0)).toBe(2)
    expect(framePercent(35)).toBe(98)
    expect(framePercent(snapshotIndex(0.5))).toBe(51)
  })
  it('builds the snapshot address, escaping the channel', () => {
    expect(snapshotSrc('-1001', 7, 11)).toBe('/api/play/preview-frame/-1001/7/11?v=8')
  })
  it('falls back to a live frame at the right time, kept inside the video', () => {
    expect(liveSrc('-1', 5, 100, 0.35)).toBe('/api/play/preview/-1/5?t=35.000&w=480&pv=17')
    expect(liveSrc('-1', 5, 100, 1)).toContain('t=99.950')
    expect(liveSrc('-1', 5, 0, 0.5)).toContain('t=0.000')
    expect(liveSrc('-1', 5, Number.NaN, 0.5)).toContain('t=0.000')
  })
  it('only warns once every frame has settled and one failed', () => {
    expect(incomplete(['snapshot', 'live', 'snapshot', 'snapshot'])).toBe(false)
    expect(incomplete(['snapshot', 'failed', 'loading', 'snapshot'])).toBe(false)
    expect(incomplete(['snapshot', 'failed', 'snapshot', 'snapshot'])).toBe(true)
  })
  it('asks the classic question for a performer or a studio', () => {
    expect(previewQuestion('performer', 'Ava')).toBe('Does this video belong to Ava? Confirming links only this performer. Use Edit Video or Scene Match Review for full scene identification.')
    expect(previewQuestion('studio', 'Northlight')).toContain('only this studio')
  })
})
