import { beforeEach, describe, expect, it } from 'vitest'
import { activeText, initialChoice, loadPrefs, mergeCues, parseVtt, savePrefs, stampSeconds, windowStart, type TrackInfo } from './tracks'

const info: TrackInfo = {
  audio: [{ index: 1, ordinal: 0, label: 'English', language: 'eng', default: true }, { index: 2, ordinal: 1, label: 'Spanish', language: 'spa', default: false }],
  subtitles: [{ index: 3, ordinal: 0, label: 'English', language: 'eng', text: true, forced: false, default: false }, { index: 4, ordinal: 1, label: 'Pic', language: 'spa', text: false, forced: false, default: false }],
  captions: { available: true, engine: 'x', install: '' },
}

describe('subtitle parsing', () => {
  it('reads VTT and SRT stamps', () => {
    expect(stampSeconds('00:01:02.500')).toBe(62.5)
    expect(stampSeconds('01:02,5')).toBe(62.5)
  })
  it('parses cues, strips tags, ignores header and notes', () => {
    const cues = parseVtt('WEBVTT\n\nNOTE x\n\n1\n00:00:01.000 --> 00:00:02.000 align:start\n<i>Hello</i> &amp; hi\n\n00:00:03.000 --> 00:00:04.000\nBye\n')
    expect(cues).toEqual([{ start: 1, end: 2, text: 'Hello & hi' }, { start: 3, end: 4, text: 'Bye' }])
  })
  it('finds the cue showing at a time', () => {
    const cues = parseVtt('WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nA\n\n00:00:02.000 --> 00:00:04.000\nB\n')
    expect(activeText(cues, 0.5)).toBe('')
    expect(activeText(cues, 2.5)).toBe('A\nB')
    expect(activeText(cues, 3.5)).toBe('B')
  })
  it('merges without duplicates', () => {
    const a = [{ start: 1, end: 2, text: 'x' }]
    expect(mergeCues(a, [{ start: 1, end: 2, text: 'x' }, { start: 0, end: 1, text: 'y' }]).map((c) => c.text)).toEqual(['y', 'x'])
  })
  it('windows by 30 seconds', () => { expect(windowStart(59.9)).toBe(30); expect(windowStart(-3)).toBe(0) })
})

describe('initial choice', () => {
  beforeEach(() => localStorage.clear())
  it('keeps the default audio and subtitles off with no saved preference', () => {
    expect(initialChoice(info, {})).toEqual({ audio: null, sub: { kind: 'off' } })
  })
  it('picks the preferred audio language and subtitle track', () => {
    expect(initialChoice(info, { audioLanguage: 'spa', sub: 'track', subLanguage: 'eng' })).toEqual({ audio: 2, sub: { kind: 'track', index: 3 } })
  })
  it('only restores live captions when the engine exists, and skips picture subtitles', () => {
    expect(initialChoice({ ...info, captions: { ...info.captions, available: false } }, { sub: 'live' }).sub).toEqual({ kind: 'off' })
    expect(initialChoice(info, { sub: 'track', subLanguage: 'spa' }).sub).toEqual({ kind: 'off' })
  })
  it('saves and loads preferences', () => { savePrefs({ sub: 'live' }); expect(loadPrefs()).toEqual({ sub: 'live' }) })
})
