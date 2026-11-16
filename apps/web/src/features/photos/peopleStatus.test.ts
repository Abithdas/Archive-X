import { describe, expect, it } from 'vitest'
import { detailLine, learningLine, namesLine, problemOf, reasonText, type NameList } from './peopleStatus'

const prov = (o: Partial<NameList['providers'][string]> = {}) => ({ configured: true, status: 'idle', count: 0, last_finished: '', last_error: '', page: 0, ...o })
const list = (providers: NameList['providers'], total = 0): NameList => ({ total, ready: total > 0, providers })

describe('reasonText', () => {
  it('puts a rejected key in plain words', () => {
    expect(reasonText('HTTP 401: Unauthenticated')).toMatch(/key was not accepted/)
    expect(reasonText('HTTP 403')).toMatch(/key was not accepted/)
  })
  it('explains busy, broken and unreachable services', () => {
    expect(reasonText('HTTP 429: slow down')).toMatch(/too many requests/)
    expect(reasonText('HTTP 503')).toMatch(/try again/)
    expect(reasonText('connection timed out')).toMatch(/no connection/)
  })
  it('handles empty and very long reasons', () => {
    expect(reasonText('')).toBe('an unknown problem')
    expect(reasonText('x'.repeat(300)).length).toBeLessThanOrEqual(110)
  })
})

describe('problemOf', () => {
  it('names the failing provider only when it is set up', () => {
    expect(problemOf(list({ stashdb: prov({ status: 'error', last_error: 'HTTP 401' }) }))).toEqual({ provider: 'StashDB', reason: 'the key was not accepted, check it in Settings' })
    expect(problemOf(list({ stashdb: prov({ configured: false, status: 'error' }) }))).toBeNull()
    expect(problemOf(undefined)).toBeNull()
  })
})

describe('namesLine', () => {
  it('is empty while unknown and asks for a key when none is set', () => {
    expect(namesLine(undefined)).toBe('')
    expect(namesLine(list({ stashdb: prov({ configured: false }) }))).toMatch(/Add a StashDB or ThePornDB key/)
  })
  it('shows the count, progress, or that collection has begun', () => {
    expect(namesLine(list({ stashdb: prov() }, 1240))).toBe('1,240 names on this PC')
    expect(namesLine(list({ stashdb: prov({ status: 'running', page: 12 }) }, 300))).toBe('300 names on this PC · reading StashDB (page 12)')
    expect(namesLine(list({ stashdb: prov() }, 0))).toMatch(/Collecting/)
  })
})

describe('learningLine and detailLine', () => {
  it('says what was learned, singular and plural, and nothing before that', () => {
    expect(learningLine({ faces: 9, confirmed: 0, removed: 0 })).toBe('')
    expect(learningLine({ faces: 9, confirmed: 1, removed: 0 })).toBe('learning from 1 confirmed and 0 removed face')
    expect(learningLine({ faces: 9, confirmed: 5, removed: 2 })).toBe('learning from 5 confirmed and 2 removed faces')
  })
  it('joins the two on one line', () => {
    expect(detailLine(list({ stashdb: prov() }, 10), { faces: 1, confirmed: 2, removed: 1 })).toBe('10 names on this PC · learning from 2 confirmed and 1 removed faces')
    expect(detailLine(undefined, undefined)).toBe('')
  })
})
