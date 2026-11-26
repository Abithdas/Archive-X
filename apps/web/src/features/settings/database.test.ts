import { describe, expect, it } from 'vitest'
import { compactConfirmText, compactResultMessage, databaseNote, databaseRows, whenText } from './database'

const fmt = (b: number) => `${b} B`
const state = { file_bytes: 1000, free_bytes: 250, free_percent: 25, incremental: false, last_compact: '', last_optimize: '', can_compact: true, compact_needs_bytes: 1300, disk_free_bytes: 5000, compact_recommended: true }

describe('databaseRows', () => {
  it('lists the size, the unused space and when it was last looked after', () => {
    const rows = Object.fromEntries(databaseRows(state, fmt).map((r) => [r.label, r.value]))
    expect(rows['Database size']).toBe('1000 B')
    expect(rows['Unused space inside it']).toBe('250 B (25%)')
    expect(rows['Last compacted']).toBe('never')
    expect(rows['Gives space back by itself']).toMatch(/Not yet/)
  })
  it('says so once the file gives space back by itself', () => {
    const rows = Object.fromEntries(databaseRows({ ...state, incremental: true, last_compact: '2026-11-20T10:00:00' }, fmt).map((r) => [r.label, r.value]))
    expect(rows['Gives space back by itself']).toMatch(/Yes/)
    expect(rows['Last compacted']).not.toBe('never')
  })
  it('copes with an answer that has nothing in it', () => {
    expect(() => databaseRows({}, fmt)).not.toThrow()
  })
})

describe('databaseNote', () => {
  it('explains why compacting cannot run', () => expect(databaseNote({ ...state, can_compact: false }, fmt)).toMatch(/needs about 1300 B .* 5000 B is free/))
  it('recommends compacting when it would help', () => expect(databaseNote(state, fmt)).toMatch(/give back unused space/))
  it('says nothing is needed otherwise', () => expect(databaseNote({ ...state, compact_recommended: false }, fmt)).toMatch(/Nothing to do/))
})

describe('compact texts', () => {
  it('warns that writing pauses and how much room it needs', () => expect(compactConfirmText(state, fmt)).toMatch(/Writing pauses.*1300 B/s))
  it('reports what was given back, or that nothing was', () => {
    expect(compactResultMessage({ saved_bytes: 500, seconds: 3.2 }, fmt)).toBe('Compacted in 3.2 s · 500 B given back')
    expect(compactResultMessage({ saved_bytes: 0, seconds: 1 }, fmt)).toMatch(/already compact/)
  })
})

describe('whenText', () => {
  it('is never for nothing or for text that is not a date', () => { expect(whenText('')).toBe('never'); expect(whenText('soon')).toBe('never') })
})
