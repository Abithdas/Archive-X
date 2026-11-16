import { describe, expect, it } from 'vitest'
import { actions, knowledgeLine } from './findingsLogic'

describe('findings', () => {
  it('offers the right next steps for each state', () => {
    expect(actions('OPEN').map((a) => a.to)).toEqual(['ACCEPTED', 'REJECTED'])
    expect(actions('ACCEPTED').map((a) => a.to)).toEqual(['FIXED', 'REJECTED'])
    expect(actions('FIXED').map((a) => a.to)).toEqual(['OPEN'])
    expect(actions('REJECTED').map((a) => a.label)).toEqual(['Reopen'])
  })
  it('says what has been read, in words', () => {
    expect(knowledgeLine(undefined)).toMatch(/not read/)
    expect(knowledgeLine({ changelog: 1, docs: 0, code: 2300 })).toBe('It has read 1 changelog note, 2,300 code descriptions.')
    expect(knowledgeLine({ changelog: 12, docs: 3, code: 1 })).toBe('It has read 12 changelog notes, 3 docs sections, 1 code description.')
  })
})
