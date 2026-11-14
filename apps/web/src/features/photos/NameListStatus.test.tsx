import { describe, expect, it } from 'vitest'
import { ageText, nameListLine, type NameList } from './NameListStatus'

const P = { configured: true, status: 'idle', count: 0, last_finished: '', last_error: '', page: 1 }
const list = (over: Partial<NameList['providers']['stashdb']> = {}, total = 1234): NameList => ({ total, ready: total > 0, providers: { stashdb: { ...P, count: total, last_finished: '2026-01-01T10:00:00Z', ...over }, theporndb: { ...P, configured: false } } })

describe('nameListLine', () => {
  const now = Date.parse('2026-01-01T12:00:00Z')
  it('says how many names are kept and when they were refreshed', () => expect(nameListLine(list(), now)).toBe('1,234 performer names on this PC · updated 2 hours ago'))
  it('explains what to do when no provider key is set', () => expect(nameListLine({ total: 0, ready: false, providers: { stashdb: { ...P, configured: false } } }, now)).toMatch(/Add a StashDB or ThePornDB key/))
  it('shows progress while a provider is being read', () => expect(nameListLine(list({ status: 'running', page: 12 }), now)).toMatch(/updating StashDB… \(page 12\)/))
  it('shows a failed refresh and that it will retry', () => expect(nameListLine(list({ status: 'error', last_error: 'HTTP 429' }), now)).toMatch(/update failed \(HTTP 429\); it will retry/))
  it('has simple ages', () => { expect(ageText(30_000)).toBe('just now'); expect(ageText(10 * 60_000)).toBe('10 minutes ago'); expect(ageText(3 * 86_400_000)).toBe('3 days ago') })
})
