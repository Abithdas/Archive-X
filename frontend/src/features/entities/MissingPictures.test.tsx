import { describe, expect, it } from 'vitest'
import { missingPicturesText } from './MissingPictures'

describe('missing pictures wording', () => {
  it('says how many have no picture and that they are fetched on their own', () => {
    expect(missingPicturesText(1, 'performers', false)).toBe('1 performer has no picture yet. They are fetched in the background every few hours.')
    expect(missingPicturesText(1200, 'studios', false)).toMatch(/^1,200 studios have no picture yet/)
  })
  it('shows progress while fetching', () => expect(missingPicturesText(37, 'performers', true)).toBe('Fetching pictures… 37 still to do.'))
})
