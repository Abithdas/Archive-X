import { describe, expect, it } from 'vitest'
import { MAX_MERGE, autoConfirm, autoGroups, autoLabel, autoOutcome, autoToast, canMerge, groupFooter, headingFor, identityText, isProtected, junkCheck, junkConfirm, junkOutcome, kindInfo, manualConfirm, memberLine, mergeIds, mergedMessage, similarityLabel, totalsLine, truncatedNote, type Group } from './audit'

const m = (id: number, name: string, over: object = {}) => ({ id, name, video_count: 2, ...over })

describe('summary', () => {
  it('counts the groups safe to auto-merge and words the totals', () => {
    const a = { performer_count: 1234, candidate_merge_groups: 2, review_groups: 1, suggestion_groups: 5, junk_fragment_groups: 3, unmatched_performer_count: 9, groups: [{ kind: 'strong_merge', auto_merge_eligible: true }, { kind: 'review' }, { kind: 'strong_merge', auto_merge_eligible: true }] }
    expect(autoGroups(a)).toHaveLength(2)
    expect(autoLabel(2)).toBe('Auto-merge high-confidence (2)')
    expect(autoLabel(0)).toBe('No safe auto-merges')
    expect(totalsLine(a)).toBe(`Scanned ${(1234).toLocaleString()} performer records · 2 high-confidence groups safe to auto-merge · 2 alias/provider candidates · 1 identity-conflict groups · 5 similar-name suggestions · 3 likely filename/title fragments · 9 records with no linked duplicate evidence`)
  })
  it('mentions when suggestions were capped', () => {
    expect(truncatedNote({ suggestions_truncated: true, suggestion_groups_returned: 50, suggestion_groups: 400 })).toContain('Showing 50 of 400 similar-name suggestions.')
    expect(truncatedNote({})).toBe('')
  })
  it('titles each kind of group, falling back to review wording', () => {
    expect(headingFor('strong_merge')).toBe('Strong candidates')
    expect(headingFor('junk_fragment')).toBe('Likely filename/title fragments')
    expect(headingFor('weird')).toBe('Candidates')
    expect(kindInfo('suggest_review').treatment).toBe('Suggest for review only')
    expect(kindInfo('weird').title).toBe('Conflicting identity evidence')
  })
})

describe('group display', () => {
  it('describes members', () => {
    expect(memberLine(m(3, 'A', { has_image: true }))).toBe('ID 3 · 2 videos · artwork')
    expect(memberLine({ id: 4, video_count: 1 })).toBe('ID 4 · 1 video')
    expect(identityText({ id: 1, provider_ids: { stashdb: ['a', 'b'], tpdb: 'x', empty: [] } })).toBe('stashdb: a, b · tpdb: x')
    expect(identityText({ id: 1 })).toBe('No saved provider ID')
    expect(similarityLabel({ kind: 'x', similarity: 0.876 })).toBe(' · Similarity 88%')
    expect(similarityLabel({ kind: 'x' })).toBe('')
  })
  it('protects the Unknown Performer group', () => {
    expect(isProtected({ kind: 'review', contains_unknown_performer: true })).toBe(true)
    expect(groupFooter({ kind: 'review', contains_unknown_performer: true })).toContain('Unknown Performer is protected')
    expect(groupFooter({ kind: 'review' })).toContain('choose exactly one “Keep” record')
  })
})

describe('manual merge', () => {
  const g: Group = { kind: 'strong_merge', performers: [m(1, 'Ava Stone'), m(2, 'Ava Stonee'), m(3, 'Mia')] }
  it('never includes the keeper, duplicates or bad ids', () => {
    expect(mergeIds(1, [1, 2, 2, 3, 0, -4])).toEqual([2, 3])
    expect(canMerge(null, [2])).toBe(false)
    expect(canMerge(1, [1])).toBe(false)
    expect(canMerge(1, [1, 2])).toBe(true)
  })
  it('asks first, naming the keeper and the records to merge', () => {
    const c = manualConfirm(g, 1, [2, 3])
    expect(c).toMatchObject({ ok: true, title: 'Confirm performer cleanup', okLabel: 'Merge 2' })
    if (c.ok) { expect(c.text).toContain('Merge 2 selected record(s) into “Ava Stone”?'); expect(c.text).toContain('• Ava Stonee\n• Mia'); expect(c.text).toContain('permanent change') }
  })
  it('truncates a long list and names unknown ids', () => {
    const big: Group = { kind: 'x', performers: [m(1, 'K'), ...Array.from({ length: 10 }, (_, i) => m(100 + i, `P${i}`))] }
    const c = manualConfirm(big, 1, Array.from({ length: 10 }, (_, i) => 100 + i))
    if (c.ok) expect(c.text).toContain('• …and 2 more')
    const d = manualConfirm(g, 1, [999])
    if (d.ok) expect(d.text).toContain('• #999')
  })
  it('refuses without a keeper or duplicates, and above the 500 limit', () => {
    expect(manualConfirm(g, 99, [2])).toEqual({ ok: false, message: 'Choose one keeper and at least one different performer to merge.' })
    expect(manualConfirm(g, 1, [])).toMatchObject({ ok: false })
    expect(manualConfirm(g, 1, Array.from({ length: MAX_MERGE + 1 }, (_, i) => i + 10))).toEqual({ ok: false, message: 'Merge no more than 500 duplicate performers in one operation.' })
  })
  it('words the result', () => {
    expect(mergedMessage({ merged_count: 2, name: 'Ava' }, 5, 'x')).toBe('Merged 2 performer record(s) into “Ava”.')
    expect(mergedMessage({}, 3, 'Ava')).toBe('Merged 3 performer record(s) into “Ava”.')
  })
})

describe('automatic cleanup', () => {
  it('states exactly how many records in how many groups, and the rules', () => {
    const c = autoConfirm({ groups: [{ kind: 'strong_merge', auto_merge_eligible: true, performers: [m(1, 'a'), m(2, 'b'), m(3, 'c')] }, { kind: 'strong_merge', auto_merge_eligible: true, performers: [m(4, 'd'), m(5, 'e')] }, { kind: 'review', performers: [m(6, 'f'), m(7, 'g')] }] })
    expect(c).toMatchObject({ eligible: 2, rows: 3, okLabel: 'Auto-merge 3', title: 'Auto-clean high-confidence duplicates' })
    expect(c.text).toContain('Automatically merge 3 duplicate performer record(s) in 2 high-confidence group(s)?')
    expect(c.text).toContain('rolled back together if any merge fails')
    expect(autoConfirm({ groups: [] }).eligible).toBe(0)
  })
})

describe('automatic cleanup result', () => {
  it('reports what merged and what was deliberately left alone', () => {
    const r = { merged_performer_count: 4, merged_group_count: 2, ambiguous_groups_left_for_review: 3, protected_unknown_group_count: 1 }
    expect(autoOutcome(r)).toBe('Auto-cleanup complete: merged 4 performer record(s) in 2 group(s). Left 3 ambiguous group(s) separate for review; protected Unknown Performer groups: 1.')
    expect(autoToast(r)).toBe('Auto-cleanup merged 4 high-confidence duplicate performer record(s). Ambiguous matches were left for review.')
    expect(autoOutcome({})).toContain('merged 0 performer record(s) in 0 group(s)')
  })
})

describe('filename fragments', () => {
  const frag = (over: Partial<Group> = {}): Group => ({ kind: 'junk_fragment', performers: [m(7, '1080p WEB', { video_count: 0 })], suggested_target: { id: 2, name: 'Mia' }, ...over })
  it('allows delete only for an unlinked record, and merge only with a target', () => {
    expect(junkCheck(frag(), 'delete')).toBe('')
    expect(junkCheck(frag({ performers: [m(7, 'x', { video_count: 3 })] }), 'delete')).toBe('This performer still has linked videos; merge them first.')
    expect(junkCheck(frag({ suggested_target: null }), 'merge')).toBe('No suggested merge target is available.')
    expect(junkCheck(frag(), 'merge')).toBe('')
    expect(junkCheck({ kind: 'junk_fragment', performers: [] }, 'merge')).toBe('This audit record is stale. Refresh the audit before changing it.')
  })
  it('words the confirmations and outcomes', () => {
    expect(junkConfirm(frag(), 'merge')).toMatchObject({ title: 'Merge filename fragment', okLabel: 'Merge performer' })
    expect(junkConfirm(frag(), 'merge').text).toBe('Merge “1080p WEB” into “Mia”? All video links, aliases, provider identities and related metadata will be preserved on the target.')
    expect(junkConfirm(frag(), 'delete')).toMatchObject({ title: 'Delete unlinked fragment', okLabel: 'Delete record' })
    expect(junkConfirm(frag({ suggested_target: null }), 'merge').text).toContain('“the selected target”')
    expect(junkOutcome('merge', 'X', { name: 'Mia R.' }, 'Mia')).toBe('Merged likely fragment “X” into “Mia R.”.')
    expect(junkOutcome('delete', 'X', {}, '')).toBe('Deleted unlinked junk record “X”.')
  })
})
