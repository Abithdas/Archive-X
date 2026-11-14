import { describe, expect, it } from 'vitest'
import { missingGenderText, restoredText } from './MissingGender'

describe('missing gender wording', () => {
  it('says how many are hidden and where they still are', () => {
    expect(missingGenderText(1)).toBe('1 performer has no gender, so it is hidden under Female, Male and Trans. They are still under All.')
    expect(missingGenderText(8)).toMatch(/^8 performers have no gender, so they are hidden/)
  })
  it('says what was restored and what is left to do by hand', () => {
    expect(restoredText({ restored: 6, still_blank: 0 })).toBe('Restored 6 from provider data.')
    expect(restoredText({ restored: 0, still_blank: 2 })).toMatch(/^Nothing could be restored from provider data\. 2 still need a gender: open All/)
  })
})
