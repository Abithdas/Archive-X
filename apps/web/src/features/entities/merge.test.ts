import { describe, expect, it } from 'vitest'
import { IDLE, canCommit, commitLabel, confirmation, duplicatesOf, hint, modeLabel, mergeBody, pick, sortBy, successMessage, toggleMode, type MergeState } from './merge'

const L = [{ id: 1, name: 'Ava Stone' }, { id: 2, name: 'Ava  Stone' }, { id: 3, name: 'Mia' }]
const on: MergeState = { active: true, keepId: null, mergeIds: [], busy: false }

describe('merge mode', () => {
  it('turns on with a hint and off with a clean slate', () => {
    const a = toggleMode(IDLE)
    expect(a.state).toEqual(on)
    expect(a.message).toBe('Select one performer to keep, then select every duplicate to merge into it.')
    const b = toggleMode({ ...on, keepId: 1, mergeIds: [2] })
    expect(b.state).toEqual(IDLE)
    expect(b.message).toBe('')
  })
  it('cannot be toggled while a merge is running', () => {
    const s = { ...on, busy: true }
    expect(toggleMode(s).state).toBe(s)
  })
  it('first pick is the keeper; then picks add and remove duplicates', () => {
    const a = pick(on, 1, L)
    expect(a.state.keepId).toBe(1)
    expect(a.message).toBe('Keep “Ava Stone”. Now select all duplicates to merge into it.')
    const b = pick(a.state, 2, L)
    expect(b.state.mergeIds).toEqual([2])
    const c = pick(pick(b.state, 3, L).state, 2, L)
    expect(c.state.mergeIds).toEqual([3])   // 2 toggled off
  })
  it('picking the keeper again changes nothing and says so', () => {
    const r = pick({ ...on, keepId: 1 }, 1, L)
    expect(r.state).toEqual({ ...on, keepId: 1 })
    expect(r.message).toBe('This is the keeper. Select other performer cards to add them as duplicates.')
  })
  it('clears the choice when the keeper has disappeared from the list', () => {
    const r = pick({ ...on, keepId: 99, mergeIds: [2] }, 3, L)
    expect(r.state).toMatchObject({ keepId: null, mergeIds: [] })
    expect(r.message).toBe('Keeper record is no longer available. Select it again.')
  })
  it('refuses an unknown performer and ignores clicks when off or busy', () => {
    expect(pick(on, 42, L).message).toBe('Performer not found in the current list. Reload and try again.')
    expect(pick(IDLE, 1, L).state).toBe(IDLE)
    const busy = { ...on, busy: true }
    expect(pick(busy, 1, L).state).toBe(busy)
  })
  it('does not mutate the previous state', () => {
    const s: MergeState = { ...on, keepId: 1, mergeIds: [2] }
    pick(s, 3, L)
    expect(s.mergeIds).toEqual([2])
  })
})

describe('labels and rules', () => {
  it('words the buttons and the hint', () => {
    expect(modeLabel(false)).toBe('Merge duplicates')
    expect(modeLabel(true)).toBe('Cancel merge')
    expect(commitLabel(3)).toBe('Merge selected (3)')
    expect(hint(IDLE, L)).toBe('')
    expect(hint(on, L)).toBe('Select the canonical performer to keep first.')
    expect(hint({ ...on, keepId: 1, mergeIds: [2] }, L)).toBe('Keep: Ava Stone · 1 duplicate selected. Click cards to add/remove, then merge.')
    expect(hint({ ...on, keepId: 1, mergeIds: [2, 3], busy: true }, L)).toBe('Merging 2 duplicates atomically…')
  })
  it('can commit only with a present keeper and at least one duplicate', () => {
    expect(canCommit(on, L)).toBe(false)
    expect(canCommit({ ...on, keepId: 1 }, L)).toBe(false)
    expect(canCommit({ ...on, keepId: 1, mergeIds: [2] }, L)).toBe(true)
    expect(canCommit({ ...on, keepId: 99, mergeIds: [2] }, L)).toBe(false)
    expect(canCommit({ ...on, keepId: 1, mergeIds: [2], busy: true }, L)).toBe(false)
    expect(canCommit({ ...IDLE, keepId: 1, mergeIds: [2] }, L)).toBe(false)
  })
  it('never sends the keeper as its own duplicate', () => {
    expect(duplicatesOf({ ...on, keepId: 1, mergeIds: [1, 2, 3] })).toEqual([2, 3])
    expect(mergeBody(1, [2, 3])).toEqual({ keep_id: 1, merge_ids: [2, 3] })
  })
  it('shows the merge for confirmation, truncating a long list', () => {
    const c = confirmation(L[0]!, [2, 3], L)
    expect(c.title).toBe('Merge duplicate performers')
    expect(c.ok).toBe('Merge 2 selected')
    expect(c.text).toContain('Merge 2 duplicate performer records into “Ava Stone”?')
    expect(c.text).toContain('• Ava  Stone (ID 2)\n• Mia (ID 3)')
    expect(c.text).toContain('rolled back if any merge fails. This cannot be undone.')
    const many = Array.from({ length: 11 }, (_, i) => ({ id: 100 + i, name: `P${i}` }))
    expect(confirmation(L[0]!, many.map((m) => m.id), many).text).toContain('• …and 3 more')
    expect(confirmation(L[0]!, [999], L).text).toContain('• #999')
    expect(confirmation(L[0]!, [2], L).text).toContain('Merge 1 duplicate performer record into')
  })
  it('words the result', () => {
    expect(successMessage({ merged_count: 2, name: 'Ava Stone' }, 2, 'x')).toBe('Merged 2 duplicates into “Ava Stone”. Video associations and provider IDs were preserved.')
    expect(successMessage({}, 1, 'Ava')).toBe('Merged 1 duplicate into “Ava”. Video associations and provider IDs were preserved.')
  })
})

describe('sorting', () => {
  const people = [{ id: 1, name: 'Zoe' }, { id: 2, name: 'amy' }, { id: 3, name: 'Émile' }]
  it('keeps the order as given by default (a copy)', () => {
    const out = sortBy(people, 'default')
    expect(out).toEqual(people)
    expect(out).not.toBe(people)
  })
  it('sorts by name, ignoring case and accents', () => {
    expect(sortBy(people, 'name_asc').map((p) => p.name)).toEqual(['amy', 'Émile', 'Zoe'])
    expect(sortBy(people, 'name_desc').map((p) => p.name)).toEqual(['Zoe', 'Émile', 'amy'])
  })
})
