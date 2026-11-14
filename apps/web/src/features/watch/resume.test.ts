import { beforeEach, describe, expect, it } from 'vitest'
import { LAST_KEY, clearStored, dueForSave, finishedBody, isLastPlayed, labelFor, markPlayed, outcome, progressBody, progressPct, promptPosition, readStored, resumeStorageKey, saveStored, shouldSaveLocal, timeLine, vkey } from './resume'

beforeEach(() => localStorage.clear())

describe('what to save', () => {
  it('saves only the last-played video, away from the very start and the very end', () => {
    expect(shouldSaveLocal(true, 120, 3600)).toBe(true)
    expect(shouldSaveLocal(false, 120, 3600)).toBe(false)
    expect(shouldSaveLocal(true, 20, 3600)).toBe(false)     // not past 20 s
    expect(shouldSaveLocal(true, 21, 3600)).toBe(true)
    expect(shouldSaveLocal(true, 3576, 3600)).toBe(false)   // within the last 25 s
    expect(shouldSaveLocal(true, 3574, 3600)).toBe(true)
    expect(shouldSaveLocal(true, 120, 0)).toBe(false)       // unknown duration
  })
  it('throttles to once every 5 seconds', () => {
    expect(dueForSave(5000, 0)).toBe(true)
    expect(dueForSave(4999, 0)).toBe(false)
    expect(dueForSave(10_000, 5000)).toBe(true)
  })
  it('does not count a seek as watch time', () => {
    expect(progressBody('-1', 7, 105, 600, 100)).toEqual({ channel_id: '-1', message_id: 7, position: 105, duration: 600, watch_delta: 5 })
    expect(progressBody('-1', 7, 500, 600, 100).watch_delta).toBe(0)      // jumped 400 s
    expect(progressBody('-1', 7, 115, 600, 100).watch_delta).toBe(15)     // exactly 15 still counts
    expect(progressBody('-1', 7, 90, 600, 100).watch_delta).toBe(0)       // backwards
    expect(finishedBody('-1', 7, 600)).toEqual({ channel_id: '-1', message_id: 7, position: 600, duration: 600, watch_delta: 0 })
  })
})

describe('when to ask', () => {
  const base = { enabled: true, isLast: true, stored: 300, duration: 3600 }
  it('offers the saved position when it is meaningful', () => {
    expect(promptPosition(base)).toBe(300)
    expect(promptPosition({ ...base, duration: 0 })).toBe(300)   // duration unknown: still offer
  })
  it('stays quiet when disabled, from the queue, not the last played, or not worth resuming', () => {
    expect(promptPosition({ ...base, enabled: false })).toBeNull()
    expect(promptPosition({ ...base, fromQueue: true })).toBeNull()
    expect(promptPosition({ ...base, isLast: false })).toBeNull()
    expect(promptPosition({ ...base, stored: 29 })).toBeNull()
    expect(promptPosition({ ...base, stored: 30 })).toBe(30)
    expect(promptPosition({ ...base, stored: 3581 })).toBeNull()   // within the last 20 s
    expect(promptPosition({ ...base, stored: 3579 })).toBe(3579)
    expect(promptPosition({ ...base, stored: Number.NaN })).toBeNull()
  })
  it('turns each choice into a start position and play state', () => {
    expect(outcome('resume', 300)).toEqual({ startAt: 300, autoPlay: true })
    expect(outcome('start', 300)).toEqual({ startAt: 0, autoPlay: true })
    expect(outcome('cancel', 300)).toEqual({ startAt: 0, autoPlay: false })
  })
  it('words the dialog like the classic page', () => {
    expect(labelFor(125)).toBe('2:05')
    expect(labelFor(3725)).toBe('1:02:05')
    expect(progressPct(300, 600)).toBe(50)
    expect(progressPct(300, 0)).toBe(0)
    expect(progressPct(900, 600)).toBe(100)
    expect(timeLine('2:05', 3725)).toBe('2:05 / 1:02:05')
    expect(timeLine('2:05', 0)).toBe('2:05')
  })
})

describe('storage (shared with the classic UI)', () => {
  it('uses the classic keys', () => {
    expect(vkey('-100', 5)).toBe('-100:5')
    expect(resumeStorageKey('-100', 5)).toBe('ta_resume:-100:5')
    expect(LAST_KEY).toBe('ta_last_played_video')
  })
  it('round-trips a position as whole seconds and tracks the last-played video', () => {
    expect(readStored('-1', 1)).toBe(0)
    saveStored('-1', 1, 321.9)
    expect(readStored('-1', 1)).toBe(321)
    expect(isLastPlayed('-1', 1)).toBe(false)
    markPlayed('-1', 1)
    expect(isLastPlayed('-1', 1)).toBe(true)
    expect(isLastPlayed('-1', 2)).toBe(false)
  })
  it('clearing removes the position, and the last-played marker only if it was this video', () => {
    markPlayed('-1', 1); saveStored('-1', 1, 100)
    clearStored('-1', 2)
    expect(isLastPlayed('-1', 1)).toBe(true)
    clearStored('-1', 1)
    expect(readStored('-1', 1)).toBe(0)
    expect(isLastPlayed('-1', 1)).toBe(false)
  })
  it('never throws when storage is blocked', () => {
    const real = Storage.prototype.getItem
    Storage.prototype.getItem = () => { throw new Error('blocked') }
    expect(readStored('-1', 1)).toBe(0)
    expect(isLastPlayed('-1', 1)).toBe(false)
    Storage.prototype.getItem = real
  })
})
