import { describe, expect, it } from 'vitest'
import { listProgress } from './OperationsPage'

describe('jobs list progress', () => {
  it('counts files when the job reports a total', () => {
    expect(listProgress({ total: 20, completed: 5, progress: 99 })).toEqual({ known: true, percent: 25, text: '25% · 5 / 20' })
  })
  it('uses the job\'s own percentage when it has no total (Metadata & Assets and similar)', () => {
    expect(listProgress({ progress: 42 })).toEqual({ known: true, percent: 42, text: '42%' })
    expect(listProgress({ progress: 3.456 }).text).toBe('3.5%')
    expect(listProgress({ progress: 100.5 }).percent).toBe(100)
  })
  it('only says it cannot measure when there is nothing to go on', () => {
    expect(listProgress({})).toEqual({ known: false, percent: 0, text: 'progress not yet measurable' })
    expect(listProgress({ progress: 0 }).known).toBe(false)
    expect(listProgress({ progress: Number.NaN }).known).toBe(false)
    expect(listProgress({ total: 0, completed: 3 }).known).toBe(false)
  })
  it('never goes outside 0–100', () => {
    expect(listProgress({ total: 4, completed: 9 }).percent).toBe(100)
    expect(listProgress({ total: 4, completed: -2 }).percent).toBe(0)
  })
})
