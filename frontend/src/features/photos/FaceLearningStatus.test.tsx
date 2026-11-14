import { describe, expect, it } from 'vitest'
import { faceLearningLine } from './FaceLearningStatus'

describe('faceLearningLine', () => {
  it('says what it learns from', () => expect(faceLearningLine({ faces: 900, confirmed: 1240, removed: 1 })).toMatch(/^Learning from 1,240 confirmed faces and 1 removed face\./))
  it('explains how to start when nothing has been confirmed yet', () => expect(faceLearningLine({ faces: 10, confirmed: 0, removed: 0 })).toMatch(/starts when you name people/))
  it('says nothing when the answer is not the expected shape (browser, no desktop engine)', () => { expect(faceLearningLine(undefined)).toBe(''); expect(faceLearningLine({ ok: true } as never)).toBe('') })
})
