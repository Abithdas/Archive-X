import { describe, expect, it } from 'vitest'
import { otherModeText } from './otherMode'

describe('otherModeText', () => {
  it('says nothing when the other mode has no tasks', () => {
    expect(otherModeText(undefined)).toBe('')
    expect(otherModeText(null)).toBe('')
    expect(otherModeText({ count: 0, running: 0 })).toBe('')
  })
  it('says how many are running, waiting, or both, without saying what they are', () => {
    expect(otherModeText({ count: 1, running: 1 })).toBe('A task is running in the other mode.')
    expect(otherModeText({ count: 3, running: 3 })).toBe('3 tasks are running in the other mode.')
    expect(otherModeText({ count: 1, running: 0 })).toBe('A task is waiting in the other mode.')
    expect(otherModeText({ count: 2, running: 0 })).toBe('2 tasks are waiting in the other mode.')
    expect(otherModeText({ count: 4, running: 1 })).toBe('4 tasks are in the other mode, 1 of them running.')
  })
  it('copes with odd numbers from the server', () => {
    expect(otherModeText({ count: 2, running: 9 })).toBe('2 tasks are running in the other mode.')
    expect(otherModeText({ count: Number.NaN, running: 1 })).toBe('')
    expect(otherModeText({ count: -3, running: 1 })).toBe('')
  })
})
