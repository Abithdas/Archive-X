import { describe, expect, it } from 'vitest'
import { applyMatch, candidateView, confidenceOf, hintChips, initialPeople, initialStudio, lookupAllowed, lookupBody, lookupStatus, nameKey, originLabel, pickPerformer, pickStudio, safeHttpUrl, samePerformer, saveBody, saveOutcome, searchItems, stagedItems, type Item, type Person } from './quickedit'

const NONE = { performers: [] as Person[], studio: null }

describe('names', () => {
  it('compares names without accents, case, spaces or punctuation', () => {
    expect(nameKey("Zoë  O'Neil")).toBe('zoeoneil')
    expect(nameKey('zoe oneil')).toBe('zoeoneil')
    expect(nameKey(null)).toBe('')
    expect(nameKey({ name: 'A-B' })).toBe('ab')
  })
  it('treats the same name, or the same provider id, as the same performer', () => {
    expect(samePerformer({ name: 'Ava Stone' }, { name: 'ava  stone' })).toBe(true)
    expect(samePerformer({ name: 'A', provider: 'stashdb', provider_id: '7' }, { name: 'B', provider: 'STASHDB', provider_id: 7 })).toBe(true)
    expect(samePerformer({ name: 'A', provider: 'stashdb', provider_id: '7' }, { name: 'B', provider: 'tpdb', provider_id: '7' })).toBe(false)
    expect(samePerformer({ name: 'A' }, { name: 'B' })).toBe(false)
  })
  it('lists the video\'s performers once each, from names, objects or a comma string', () => {
    expect(initialPeople(['Ava', 'ava', ' ', { name: 'Mia', id: 2 }]).map((p) => p.name)).toEqual(['Ava', 'Mia'])
    expect(initialPeople('Ava Stone, Mia Rivera,')).toHaveLength(2)
    expect(initialPeople(undefined)).toEqual([])
    expect(initialStudio({ site_name: 'Northlight', site_id: 3 })).toEqual({ name: 'Northlight', id: 3 })
    expect(initialStudio({ studio: 'S' })).toEqual({ name: 'S', id: null })
    expect(initialStudio({})).toBeNull()
  })
})

describe('search rows', () => {
  const local = [{ id: 1, name: 'Ava Stone', video_count: 4 }]
  const provider = [{ name: 'Ava Stone', provider: 'stashdb', provider_id: 'x1', exact: true }, { name: 'Ava S', provider: 'stashdb', provider_id: 'x2' }, { name: 'No id' }, { provider: 'tpdb', provider_id: '9' }]
  it('puts local first, then provider candidates (even with the same name), and flags the local match', () => {
    const rows = searchItems('ava', local, provider, NONE)
    expect(rows.map((r) => [r.kind, r.name])).toEqual([['local', 'Ava Stone'], ['provider', 'Ava Stone'], ['provider', 'Ava S'], ['create', 'ava']])   // "ava" is not an exact local name, so "create" is offered
    expect(rows[1]!.localMatch).toBe(true)
    expect(rows[2]!.localMatch).toBe(false)
  })
  it('adds "create new" only when there is no exact local match and it is not already chosen', () => {
    expect(searchItems('Brand New', [], [], NONE).at(-1)).toMatchObject({ create: true, name: 'Brand New' })
    expect(searchItems('ava stone', local, [], NONE).some((r) => r.create)).toBe(false)
    expect(searchItems('Zed', [], [], { performers: [{ name: 'zed' }], studio: null }).some((r) => r.create)).toBe(false)
    expect(searchItems('Zed', [], [], { performers: [], studio: { name: 'Zed' } }).some((r) => r.create)).toBe(false)
  })
  it('needs two characters for providers and for create', () => {
    expect(searchItems('a', [], provider, NONE)).toEqual([])
    expect(searchItems('a', local, provider, NONE).map((r) => r.kind)).toEqual(['local'])
  })
  it('never shows two rows for the same provider identity, and keeps "create" within 24 rows', () => {
    const dup = [{ name: 'X', provider: 'stashdb', provider_id: '1' }, { name: 'X again', provider: 'stashdb', provider_id: '1' }]
    expect(searchItems('xx', [], dup, NONE).filter((r) => r.kind === 'provider')).toHaveLength(1)
    const many = Array.from({ length: 30 }, (_, i) => ({ id: i, name: `P${i}` }))
    const rows = searchItems('zzz', many, [], NONE)
    expect(rows).toHaveLength(24)
    expect(rows.at(-1)!.create).toBe(true)
  })
  it('shows what is already chosen when nothing is typed', () => {
    expect(stagedItems([{ name: 'Ava' }], null, 'performer')[0]).toMatchObject({ name: 'Ava', selected: true, kind: 'local' })
    expect(stagedItems([], { name: 'S', id: 2 }, 'studio')).toHaveLength(1)
    expect(stagedItems([], null, 'studio')).toEqual([])
  })
  it('describes where a row comes from', () => {
    expect(originLabel({ name: 'x', create: true }, 'studio')).toBe('Create a new local studio')
    expect(originLabel({ name: 'x', kind: 'provider', provider: 'stashdb', exact: true, localMatch: true }, 'performer')).toBe('STASHDB · Exact name · matches local record')
    expect(originLabel({ name: 'x', kind: 'provider', provider: 'tpdb', alias_exact: true }, 'performer')).toBe('TPDB · Alias match')
    expect(originLabel({ name: 'x', kind: 'local' }, 'performer')).toBe('Local catalogue')
  })
})

describe('choosing performers and a studio', () => {
  const ava: Person = { name: 'Ava Stone', id: 1, kind: 'local' }
  it('adds a new performer and removes one picked again', () => {
    const added = pickPerformer([], { name: 'Mia', id: 2, kind: 'local' } as Item)
    expect(added.map((p) => p.name)).toEqual(['Mia'])
    expect(pickPerformer(added, { name: 'mia', id: 2, kind: 'local' } as Item)).toEqual([])
  })
  it('adds a created performer once', () => {
    const c = { name: ' Zed ', create: true, kind: 'create' } as Item
    const once = pickPerformer([], c)
    expect(once).toEqual([{ name: 'Zed', id: null, provider: null, provider_id: null, kind: 'local' }])
    expect(pickPerformer(once, c)).toHaveLength(1)
  })
  it('enriches a chosen local performer with a provider identity instead of removing them', () => {
    const out = pickPerformer([ava], { name: 'Ava Stone', kind: 'provider', provider: 'stashdb', provider_id: 'sd1', image: 'a.png', profile: 'p' } as Item)
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ id: 1, provider: 'stashdb', provider_id: 'sd1', image: 'a.png', profile: 'p', name: 'Ava Stone' })
  })
  it('removes a performer who already has that provider identity', () => {
    const withId: Person = { ...ava, kind: 'provider', provider: 'stashdb', provider_id: 'sd1' }
    expect(pickPerformer([withId], { name: 'Ava Stone', kind: 'provider', provider: 'stashdb', provider_id: 'sd1' } as Item)).toEqual([])
  })
  it('does not mutate the list it was given', () => {
    const list = [ava]
    pickPerformer(list, { name: 'Mia', kind: 'local' } as Item)
    expect(list).toEqual([ava])
  })
  it('picks a studio, or creates one with no id', () => {
    expect(pickStudio({ name: 'Northlight', id: 3, kind: 'local' } as Item)).toMatchObject({ name: 'Northlight', id: 3 })
    expect(pickStudio({ name: ' Fresh ', create: true } as Item)).toEqual({ name: 'Fresh', id: null, provider: null, provider_id: null, kind: 'local' })
  })
})

describe('video-data matches', () => {
  it('reads confidence from a percent, a fraction or text, and picks a tone', () => {
    expect(confidenceOf(0.93)).toEqual({ text: '93%', tone: 'good' })
    expect(confidenceOf(75)).toEqual({ text: '75%', tone: 'warn' })
    expect(confidenceOf('42%')).toEqual({ text: '42%', tone: 'bad' })
    expect(confidenceOf('Review')).toEqual({ text: 'Review', tone: 'neutral' })
    expect(confidenceOf(250).text).toBe('100%')
  })
  it('only allows http(s) thumbnails', () => {
    expect(safeHttpUrl('https://x.test/a.jpg')).toBe('https://x.test/a.jpg')
    expect(safeHttpUrl('javascript:alert(1)')).toBe('')
    expect(safeHttpUrl('data:image/png;base64,AAA')).toBe('')
    expect(safeHttpUrl('')).toBe('')
  })
  it('summarises a candidate', () => {
    expect(candidateView({ title: 'T', provider: 'tpdb', studio: 'S', date: '2024-01-02', performers: ['A', 'B', 'C', 'D', 'E'], score_percent: 91, thumbnail: 'https://i/x.jpg' })).toEqual({ title: 'T', detail: 'TPDB · S · 2024-01-02', cast: 'A, B, C, D', thumb: 'https://i/x.jpg', confidence: '91%', tone: 'good' })
    expect(candidateView({})).toMatchObject({ title: 'Untitled', detail: 'PROVIDER · No studio data · Release date unknown', cast: 'No performer data', confidence: 'Review' })
  })
  it('stages the whole cast, studio and title without saving', () => {
    const r = applyMatch({ title: 'Scene', provider: 'tpdb', performer_details: [{ name: 'Ava', id: 'p1', image_url: 'a.png' }, { name: 'ava' }, { name: 'Mia' }], studio: 'S', studio_id: 4 })
    expect(r.title).toBe('Scene')
    expect(r.performers.map((p) => p.name)).toEqual(['Ava', 'Mia'])
    expect(r.performers[0]).toMatchObject({ provider: 'tpdb', provider_id: 'p1', image: 'a.png', id: null })
    expect(r.studio).toMatchObject({ name: 'S', id: 4, provider: 'tpdb' })
    expect(r.message).toBe('Provider cast staged (2 performers). No database change has been made yet.')
  })
  it('handles a match with no cast, title or studio', () => {
    const r = applyMatch({ performers: ['Solo'] })
    expect(r.title).toBeNull()
    expect(r.studio).toBeNull()
    expect(r.message).toBe('Provider cast staged (1 performer). No database change has been made yet.')
  })
})

describe('lookup', () => {
  it('needs two characters of something', () => {
    expect(lookupAllowed('a', '', '')).toBe(false)
    expect(lookupAllowed('', 'Av', '')).toBe(true)
  })
  it('lets a typed query win over the staged entity', () => {
    const base = { cid: '-1', mid: 7, title: 'T', performerQuery: '', studioQuery: '', performers: [{ name: 'Ava' }, { name: 'Mia' }], studio: { name: 'North' }, performerPicked: false, studioPicked: true }
    expect(lookupBody(base)).toMatchObject({ performer: 'Ava', performers: ['Ava', 'Mia'], studio: 'North', performer_selected: 'Ava', studio_selected_explicit: true })
    expect(lookupBody({ ...base, performerQuery: 'Zed', studioQuery: 'Alp' })).toMatchObject({ performer: 'Zed', studio: 'Alp', performer_query: 'Zed' })
    expect(lookupBody({ ...base, performers: [], studio: null })).toMatchObject({ performer: '', studio: '', performer_selected: '', studio_selected: '' })
  })
  it('words the result', () => {
    expect(lookupStatus({ matches: [{}], method: 'hash' })).toContain('1 video-data candidate found via hash.')
    expect(lookupStatus({ matches: [{}, {}], mapping_filter_names: { performer: ['Ava'], studio: ['S'] } })).toContain('Filtered to confirmed performer: Ava and studio: S.')
    expect(lookupStatus({ matches: [], filtered_count: 2 })).toContain('2 conflicting results were hidden')
    expect(lookupStatus({ matches: [], filtered_count: 1 })).toContain('1 conflicting result was hidden')
    expect(lookupStatus({ matches: [], errors: ['a', 'b', 'c'] })).toBe('No matching video data. Provider issue: a · b')
    expect(lookupStatus({ matches: [] })).toContain('try a shorter title')
  })
  it('turns filename hints into chips, preferring verified names', () => {
    expect(hintChips({ performers: ['Ava'], performer_candidates: ['x'], studio: 'S', studio_candidates: ['y'], date: '2024-01-02' })).toEqual([{ label: 'Ava', kind: 'entity' }, { label: 'S', kind: 'entity' }, { label: '2024-01-02', kind: 'date' }])
    expect(hintChips({ performer_candidates: ['x'], studio_candidates: ['y'], year: 2023 })).toEqual([{ label: 'x', kind: 'unverified' }, { label: 'y', kind: 'unverified' }, { label: '2023', kind: 'date' }])
    expect(hintChips(undefined)).toEqual([])
  })
})

describe('saving', () => {
  it('sends the title, the people and the studio, locked', () => {
    expect(saveBody('-1', 7, ' T ', [{ name: ' Ava ', id: 1 }, { name: ' ' }], { name: 'S' })).toEqual({
      channel_id: '-1', message_id: 7, title: 'T', performers: [{ name: 'Ava', id: 1, provider: null, provider_id: null, image: null, profile: null }],
      studio: { name: 'S', id: null, provider: null, provider_id: null, image: null }, lock: true,
    })
    expect(saveBody('-1', 7, '', [], null).studio).toBeNull()
  })
  it('does not send UI-only fields', () => {
    const dirty = { name: 'Ava', id: 1, count: 9, selected: true, kind: 'provider', localMatch: true, exact: true, provider_ids: '{}', provider: 'stashdb', provider_id: 'x', image: 'i', profile: 'p' } as unknown as Person
    expect(Object.keys(saveBody('-1', 7, 't', [dirty], null).performers[0]!).sort()).toEqual(['id', 'image', 'name', 'profile', 'provider', 'provider_id'])
  })
  it('closes after a synced caption', () => {
    expect(saveOutcome({ ok: true, status: 'synced' })).toEqual({ close: true, status: '', toast: 'Metadata saved and Telegram caption synchronized.' })
    expect(saveOutcome({ ok: true, status: 'unchanged' }).toast).toBe('Metadata saved; Telegram caption is already up to date.')
    expect(saveOutcome(undefined)).toMatchObject({ close: true, toast: 'Metadata saved and caption-sync request completed.' })
  })
  it('stays open and explains when only the Telegram caption failed', () => {
    const r = saveOutcome({ ok: false, status: 'permission_denied', detail: 'No edit rights' })
    expect(r.close).toBe(false)
    expect(r.status).toBe('Local metadata was saved, but Telegram caption sync failed. No edit rights')
    expect(r.toast).toBe('Metadata saved locally; Telegram edit permissions are missing.')
    expect(saveOutcome({ ok: false, status: 'partial' }).toast).toBe('Some linked Telegram captions updated; some failed.')
    expect(saveOutcome({ ok: false }).toast).toBe('Metadata saved locally; Telegram caption sync failed. Press Save changes to retry.')
  })
})
