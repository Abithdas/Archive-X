import { beforeEach, describe, expect, it } from 'vitest'
import { loadSeen, markSeen, saveSeen, unseen } from './seenNotices'

const rows = (...k: string[]) => k.map((key) => ({ key }))
beforeEach(() => localStorage.clear())

describe('seen notices', () => {
  it('counts only what has not been seen', () => {
    expect(unseen(rows('a', 'b', 'c'), ['b'])).toEqual(rows('a', 'c'))
    expect(unseen(rows('a'), ['a'])).toEqual([])
  })
  it('marking everything seen drops the count to zero, and a new notice raises it again', () => {
    const seen = markSeen([], rows('a', 'b'))
    expect(unseen(rows('a', 'b'), seen)).toEqual([])
    expect(unseen(rows('a', 'b', 'new'), seen)).toEqual(rows('new'))
  })
  it('returns the same array when nothing is new', () => {
    const seen = ['a', 'b']
    expect(markSeen(seen, rows('a'))).toBe(seen)
  })
  it('is kept in the browser, tolerates damage, and stays bounded', () => {
    saveSeen(['x', 'y'])
    expect(loadSeen()).toEqual(['x', 'y'])
    localStorage.setItem('ax_seen_notices', '{not json')
    expect(loadSeen()).toEqual([])
    localStorage.setItem('ax_seen_notices', JSON.stringify([1, 'ok', null]))
    expect(loadSeen()).toEqual(['ok'])
    expect(markSeen([], Array.from({ length: 400 }, (_, i) => ({ key: `k${i}` }))).length).toBe(300)
  })
})
