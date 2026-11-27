import { describe, expect, it } from 'vitest'
import type { LibraryItem } from '@/api/types'
import { after, queueFor, recommended } from './queue'

const v = (id: number, extra: Partial<LibraryItem> = {}) => ({ id, _cid: 'c', performers: [], studios: [], tags: [], studio: '', ...extra }) as unknown as LibraryItem
const ids = (l: LibraryItem[]) => l.map((i) => i.id)

describe('after', () => {
  it('starts after the video and wraps to the ones before it', () => expect(ids(after([v(1), v(2), v(3), v(4)], v(3)))).toEqual([4, 1, 2]))
  it('is every other video when the list does not hold it', () => expect(ids(after([v(1), v(2)], v(9)))).toEqual([1, 2]))
})

describe('recommended', () => {
  it('puts shared performers first, then the same studio, then shared tags, and never the video itself', () => {
    const cur = v(1, { performers: ['Ava', 'Bea'], studio: 'Northlight', tags: ['x'] })
    const pool = [v(2, { tags: ['x'] }), v(3, { studio: 'northlight' }), v(4, { performers: ['bea'] }), v(5, { performers: ['Ava', 'Bea'] }), v(1), v(6)]
    expect(ids(recommended(pool, cur))).toEqual([5, 4, 3, 2, 6])
  })
  it('ignores the Unknown placeholders: everyone without a name is not alike', () => {
    const cur = v(1, { performers: ['Unknown Performer'], studio: 'Unknown Studio' })
    expect(ids(recommended([v(2, { performers: ['Unknown Performer'] }), v(3)], cur))).toEqual([2, 3])      // no score: the original order stays
  })
})

describe('queueFor', () => {
  const s = { context: [v(1), v(2), v(3)], collection: [v(1), v(2), v(3), v(4)], performer: [v(3), v(7), v(8)], studio: [v(2), v(9)] }
  it('follows the list it came from', () => {
    const q = queueFor('context', v(2), s)
    expect([ids(q.next), q.previous?.id, q.source]).toEqual([[3, 1], 1, 'context'])
  })
  it('follows the channel, the performer or the studio', () => {
    expect(ids(queueFor('collection', v(3), s).next)).toEqual([4, 1, 2])
    expect(ids(queueFor('performer', v(3), s).next)).toEqual([7, 8])
    expect(ids(queueFor('studio', v(2), s).next)).toEqual([9])
  })
  it('falls back to the channel when the mode has nothing else to offer', () => {
    const none = { ...s, performer: [v(3)] }                                  // the only video of that performer
    const q = queueFor('performer', v(3), none)
    expect(q.source).toBe('collection') ; expect(ids(q.next)).toEqual([4, 1, 2])
  })
  it('uses the channel when the open video is not in the page of the library that was loaded', () => {
    const q = queueFor('context', v(4), { ...s, context: [v(1), v(2), v(3)] })
    expect(ids(q.next)).toEqual([1, 2, 3])
  })
  it('recommends from everything known', () => {
    const cur = v(3, { performers: ['Ava'] })
    const q = queueFor('recommended', cur, { context: [v(1), v(2)], collection: [], performer: [v(8, { performers: ['Ava'] })], studio: [] })
    expect(ids(q.next)).toEqual([8, 1, 2])
  })
})
