import { describe, expect, it } from 'vitest'
import { bucketSecond, choosePreview, frameIndexFor, keyStep, normaliseChapters, segmentAt, timeAt, toSegments } from './timelineLogic'

describe('normaliseChapters', () => {
  it('sorts, drops duplicates and positions outside the video, and fills missing titles', () => {
    const rows = [{ position: 120, title: 'B' }, { position: 30, title: 'A' }, { position: 30, title: 'dup' }, { position: 999, title: 'late' }, { position: -1, title: 'neg' }, { position: 60 }]
    expect(normaliseChapters(rows, 600).map((c) => `${c.position}:${c.title}`)).toEqual(['30:A', '60:Scene', '120:B'])
  })
  it('returns nothing for bad input or an unknown duration', () => {
    expect(normaliseChapters(null, 100)).toEqual([])
    expect(normaliseChapters([{ position: 1 }], 0)).toEqual([])
  })
})

describe('toSegments', () => {
  it('adds an Opening segment when the first marker is not at the start', () => {
    const s = toSegments([{ position: 30, title: 'A' }, { position: 90, title: 'B' }], 120)
    expect(s.map((x) => [x.start, x.end, x.title, x.intro])).toEqual([[0, 30, 'Opening', true], [30, 90, 'A', false], [90, 120, 'B', false]])
  })
  it('has no Opening when a marker sits at the start', () => expect(toSegments([{ position: 0, title: 'A' }], 100)).toEqual([{ start: 0, end: 100, title: 'A', intro: false }]))
  it('is empty without chapters', () => expect(toSegments([], 100)).toEqual([]))
  it('finds the segment at a time', () => expect(segmentAt(toSegments([{ position: 10, title: 'A' }], 100), 50)?.title).toBe('A'))
})

describe('frameIndexFor', () => {
  const times = [0, 100, 200, 300]
  it('picks the closest sampled frame', () => {
    expect(frameIndexFor(times, 40)).toBe(0)
    expect(frameIndexFor(times, 160)).toBe(2)
    expect(frameIndexFor(times, 999)).toBe(3)
  })
  it('is -1 with no frames', () => expect(frameIndexFor([], 5)).toBe(-1))
})

describe('timeAt', () => {
  it('maps pointer position to time and clamps to the track', () => {
    expect(timeAt(150, 100, 200, 600)).toBe(150)
    expect(timeAt(50, 100, 200, 600)).toBe(0)
    expect(timeAt(900, 100, 200, 600)).toBe(600)
    expect(timeAt(150, 100, 0, 600)).toBe(0)
  })
})

describe('keyStep', () => {
  it('steps and clamps', () => {
    expect(keyStep('ArrowRight', 10, 100)).toBe(15)
    expect(keyStep('ArrowLeft', 2, 100)).toBe(0)
    expect(keyStep('PageUp', 90, 100)).toBe(100)
    expect(keyStep('Home', 50, 100)).toBe(0)
    expect(keyStep('End', 50, 100)).toBe(100)
    expect(keyStep('x', 50, 100)).toBeNull()
  })
})

describe('seek bubble: the picture and the time always agree', () => {
  const times = [60, 360, 660, 960]
  it('rounds to a whole second inside the video', () => {
    expect(bucketSecond(12.4, 100)).toBe(12); expect(bucketSecond(12.6, 100)).toBe(13)
    expect(bucketSecond(100, 100)).toBe(100); expect(bucketSecond(99.9, 99.5)).toBe(99); expect(bucketSecond(-3, 100)).toBe(0)
  })
  it('shows the live frame, labelled with that second, once it has loaded', () => {
    expect(choosePreview(125.3, 1000, 'ok', times, true)).toEqual({ kind: 'live', time: 125, frame: -1 })
  })
  it('waits (no picture from elsewhere) while the exact frame is loading', () => {
    expect(choosePreview(125.3, 1000, 'loading', times, true)).toEqual({ kind: 'loading', time: 125, frame: -1 })
    expect(choosePreview(125.3, 1000, undefined, times, true).kind).toBe('loading')
  })
  it('falls back to the nearest stored snapshot, with its own time, when the live frame cannot be made', () => {
    expect(choosePreview(400, 1000, 'failed', times, true)).toEqual({ kind: 'snapshot', time: 360, frame: 1 })
  })
  it('uses the snapshots alone when there is no live preview', () => {
    expect(choosePreview(700, 1000, undefined, times, false)).toEqual({ kind: 'snapshot', time: 660, frame: 2 })
    expect(choosePreview(700, 1000, undefined, undefined, false)).toEqual({ kind: 'none', time: 700, frame: -1 })
  })
})
