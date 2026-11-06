import { describe, expect, it } from 'vitest'
import { keyPlaceholder, keysBody, providerCards, railRange, windowBody, windowFrom, windowProblem, windowSaved, windowStatus } from './providers'

describe('provider keys', () => {
  it('sends only typed, trimmed fields', () => {
    expect(keysBody('', '')).toEqual({})
    expect(keysBody(' tok ', '')).toEqual({ tpdb_token: 'tok' })
    expect(keysBody('', ' k ')).toEqual({ stashdb_api_key: 'k' })
    expect(keysBody('a', 'b')).toEqual({ tpdb_token: 'a', stashdb_api_key: 'b' })
  })
  it('shows the masked saved value as a placeholder', () => {
    expect(keyPlaceholder('tpdb', 'abcd…wxyz')).toBe('Saved token abcd…wxyz — leave blank to keep')
    expect(keyPlaceholder('stash', '')).toBe('saved key — leave blank to keep')
  })
})
describe('scan window', () => {
  it('defaults to 01:00–07:00 and disabled', () => {
    expect(windowFrom(undefined)).toEqual({ enabled: false, start: '01:00', end: '07:00' })
    expect(windowFrom({ enabled: true, start: '22:30', end: '05:15' })).toEqual({ enabled: true, start: '22:30', end: '05:15' })
  })
  it('validates HH:MM before sending', () => {
    expect(windowProblem({ enabled: true, start: '01:00', end: '07:00' })).toBe('')
    expect(windowProblem({ enabled: true, start: '', end: '07:00' })).not.toBe('')
    expect(windowProblem({ enabled: true, start: '25:00', end: '07:00' })).not.toBe('')
  })
  it('words the status and body like the classic page', () => {
    expect(windowStatus({ enabled: true, start: '01:00', end: '07:00' })).toBe('Enabled · 01:00–07:00')
    expect(windowStatus({ enabled: false, start: '01:00', end: '07:00' })).toBe('Disabled')
    expect(windowSaved({ enabled: false, start: '01:00', end: '07:00' })).toBe('Saved · window disabled')
    expect(windowBody({ enabled: true, start: '', end: '' })).toEqual({ enabled: true, start: '01:00', end: '07:00' })
  })
})
describe('provider status cards', () => {
  it('lists sync state, brokers, then the two cache cards', () => {
    const cards = providerCards({
      providers: [{ provider: 'tpdb', entity_type: 'scene', status: 'OK', last_sync_at: '2026-01-01', request_count: 4, error: 'rate' }, { provider: 'stashdb' }],
      broker: { tpdb: { active_slots: 1, max_concurrency: 3, requests: 9, deduplicated: 2, retries: 1, cooldown_seconds: 1.234 } },
      metadata_cache_entries: 1200, artwork_cache_entries: 7,
    })
    expect(cards.map((c) => c.title)).toEqual(['TPDB · scene', 'STASHDB · scene', 'THEPORNDB · API broker', 'Local metadata cache', 'Artwork cache'])
    expect(cards[0]).toMatchObject({ value: 'OK', note: 'Last sync: 2026-01-01 · Requests: 4', error: 'rate' })
    expect(cards[1]).toMatchObject({ value: 'IDLE', note: 'Last sync: Never · Requests: 0' })
    expect(cards[2]).toMatchObject({ value: '1/3 active', note: 'Requests: 9 · Deduplicated: 2 · Retries: 1 · Cooldown: 1.2s' })
    expect(cards[3]!.value).toBe((1200).toLocaleString())
  })
  it('is empty before data arrives and still shows caches when no provider ran', () => {
    expect(providerCards(undefined)).toEqual([])
    expect(providerCards({}).map((c) => c.title)).toEqual(['Local metadata cache', 'Artwork cache'])
  })
  it('formats the rail range', () => {
    expect(railRange([1, 2, 3], 7)).toBe('1–3 of 7')
    expect(railRange([], 7)).toBe('0 of 7')
  })
})
