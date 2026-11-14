import { describe, expect, it } from 'vitest'
import { matchLabel, matchTip } from './similarity'

describe('similar performer match chip', () => {
  it('shows percentage and traits', () => {
    expect(matchLabel({ similarity_score: 0.857, matched_detail_count: 5, compared_detail_count: 6 })).toBe('86% match · 5/6 traits')
  })
  it('clamps and tolerates missing counts', () => {
    expect(matchLabel({ similarity_score: 1.4 })).toBe('100% match · 0/0 traits')
    expect(matchLabel({ similarity_score: -1, matched_detail_count: 1, compared_detail_count: 2 })).toBe('0% match · 1/2 traits')
  })
  it('shows nothing when there is no score', () => {
    expect(matchLabel({})).toBe('')
    expect(matchLabel({ similarity_score: null })).toBe('')
    expect(matchLabel({ similarity_score: Number.NaN })).toBe('')
    expect(matchTip({})).toBe('')
  })
  it('explains the match in the tooltip', () => {
    expect(matchTip({ similarity_score: 0.5, matched_fields: ['Country', ' ', 'Age'], compared_detail_count: 6, metadata_coverage: 0.999 }))
      .toBe('Matched attributes: Country, Age. Compared 6 personal/physical attributes; metadata coverage 100%.')
    expect(matchTip({ similarity_score: 0.5, compared_detail_count: 2 })).toBe('Matched attributes: gender. Compared 2 personal/physical attributes; metadata coverage 0%.')
  })
})
