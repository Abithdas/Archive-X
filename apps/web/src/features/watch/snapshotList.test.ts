import { describe, expect, it } from 'vitest'
import { openSuggestions, reasonOf, runNote, suggestionList, type SnapshotSuggestion } from './snapshotList'

const perf: SnapshotSuggestion = { kind: 'performer', id: 4, name: 'Ava Stone', score: 0.5, evidence: { frames: 12 } }
const stud: SnapshotSuggestion = { kind: 'studio', id: 9, name: 'Northlight', score: 0.9, evidence: { corner: 'br' } }

describe('snapshot suggestions', () => {
  it('keeps only well-formed rows', () => {
    expect(suggestionList({ suggestions: [perf, { kind: 'tag', id: 1, name: 'x' }, { kind: 'studio', id: 'a', name: 'y' }, { kind: 'studio', id: 2, name: '  ' }, null] })).toEqual([perf])
    expect(suggestionList(null)).toEqual([])
    expect(suggestionList({ ok: true })).toEqual([])
  })
  it('says why in words', () => {
    expect(reasonOf(perf)).toBe('Face matched in 12 snapshots')
    expect(reasonOf({ ...perf, evidence: { frames: 1 } })).toBe('Face matched in 1 snapshot')
    expect(reasonOf(stud)).toBe('Watermark in the bottom-right corner matches this studio')
  })
  it('hides what is already chosen, by id or by name', () => {
    expect(openSuggestions([perf, stud], [], null)).toEqual([perf, stud])
    expect(openSuggestions([perf, stud], [{ name: 'Ava Stone', id: 4 }], { name: 'Northlight' })).toEqual([])
    expect(openSuggestions([perf], [{ name: 'ava  stone', id: null }], null)).toEqual([])
  })
  it('reports the run honestly, including unchecked faces', () => {
    expect(runNote({ suggestions: [], frames: 0 })).toMatch(/no snapshots/)
    expect(runNote({ suggestions: [stud], frames: 36, faces_checked: true })).toBe('Found 1 suggestion from 36 snapshots.')
    expect(runNote({ suggestions: [], frames: 36, faces_checked: false })).toMatch(/found nothing.*not on this PC/)
    expect(runNote({ suggestions: [], frames: 5 })).toMatch(/Only 5 snapshots.*at least 12/)
    expect(runNote({ suggestions: [], frames: 36, faces_checked: true, faces_known: 0, marks_known: 0 })).toMatch(/no performer has a reference face.*no studio watermark is known/)
    expect(runNote({ suggestions: [], frames: 36, faces_checked: true, faces_known: 40, marks_known: 3 })).toMatch(/no face matched any of the 40 known faces; no watermark matched any of the 3 known studios/)
    expect(runNote({ suggestions: [], frames: 36, faces_checked: true, people_has: true, studio_has: true })).toMatch(/already has performers.*already has a studio/)
  })
})
