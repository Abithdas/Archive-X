import { describe, expect, it } from 'vitest'
import { PTABS, tabsFor, birthWithAge, detailRows, isUnknownLocal, metadataLinked, prettyEnum, similarEmpty, studioCount, studiosFor } from './performerextras'

const NOW = new Date(2026, 5, 15)
const prov = (details: Record<string, unknown>) => ({ providers: [{ provider: 'stashdb', normalized: JSON.stringify({ details }) }] })
describe('performer extras', () => {
  it('adds an age only to complete, plausible dates', () => {
    expect(birthWithAge('1995-06-15', NOW)).toBe('1995-06-15 (31)'); expect(birthWithAge('1995-06-16', NOW)).toBe('1995-06-16 (30)'); expect(birthWithAge('1995', NOW)).toBe('1995')
    expect(birthWithAge('1995-02-31', NOW)).toBe('1995-02-31'); expect(birthWithAge('2090-01-01', NOW)).toBe('2090-01-01'); expect(birthWithAge('1850-01-01', NOW)).toBe('1850-01-01'); expect(birthWithAge('', NOW)).toBe('')
    expect(birthWithAge('March 3, 2000', NOW)).toBe('March 3, 2000 (26)')
  })
  it('capitalises all-caps enums only', () => { expect(prettyEnum('LARGE NATURAL')).toBe('Large Natural'); expect(prettyEnum('Fake')).toBe('Fake'); expect(prettyEnum('')).toBe(''); expect(prettyEnum('FAKE TITS')).toBe('Fake Tits') })
  it('builds the Details rows, leaving out empty ones', () => {
    const rows = detailRows({ name: 'Ava' }, { ...prov({ country: 'US', birth_date: '1995-06-15', height: '168', cup_size: 'C', breast_type: 'NATURAL' }), career_counts: [{ provider: 'StashDB', count: 40 }] }, 12, NOW)
    expect(rows).toEqual([['Country', 'US'], ['Birth date', '1995-06-15 (31)'], ['Height', '168'], ['Breast type', 'Natural'], ['Cup size', 'C'], ['Videos in Archive X', '12'], ['Scenes listed by StashDB', '40']])
    expect(detailRows({ name: 'A' }, null, 0)).toEqual([['Videos in Archive X', '0']])
  })
  it('knows linked and local profiles', () => { expect(metadataLinked({ provider_ids: '{}' }, null)).toBe(false); expect(metadataLinked({ provider_ids: '{"a":1}' }, null)).toBe(true); expect(metadataLinked({}, prov({}))).toBe(true); expect(metadataLinked({ provider_ids: {} }, { providers: [] })).toBe(false); expect(isUnknownLocal({ name: ' Unknown Performer ' })).toBe(true) })
  it('lists studios on her videos, most first', () => {
    const v = [{ studios: ['A', 'B'] }, { studios: ['B'] }, {}]
    expect(studiosFor(v, [{ id: 1, name: 'A' }, { id: 2, name: 'B' }, { id: 3, name: 'C' }]).map((s) => [s.name, s.count])).toEqual([['B', 2], ['A', 1]]); expect(studioCount(v)).toBe(2)
  })
  it('words empty similar results and lists tabs', () => { expect(similarEmpty('gender_required')).toMatch(/known gender/); expect(similarEmpty()).toMatch(/No similar performers/); expect(PTABS.map((t) => t.value)).toEqual(['videos', 'studios', 'gallery', 'similar', 'suggestions']) })
})

describe('tabsFor', () => {
  it('keeps every tab for adult content, and gives movies and shows a filmography without similar performers or suggestions', () => {
    expect(tabsFor(false)).toBe(PTABS)
    expect(tabsFor(true).map((t) => [t.value, t.label])).toEqual([['videos', 'Filmography'], ['studios', 'Studios & Networks']])
  })
})
