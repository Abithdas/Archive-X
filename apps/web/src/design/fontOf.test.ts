import { describe, expect, it } from 'vitest'
import { fontOf } from './ClearField'

describe('the font the caret is measured in', () => {
  it('is built from the separate properties, because the font shorthand comes back empty when kerning is turned off', () => {
    expect(fontOf({ fontStyle: 'normal', fontWeight: '400', fontSize: '16px', fontFamily: 'Outfit, sans-serif' })).toBe('normal 400 16px Outfit, sans-serif')
    expect(fontOf({ fontStyle: 'italic', fontWeight: '700', fontSize: '20.8px', fontFamily: '"Outfit"' })).toBe('italic 700 20.8px "Outfit"')
  })
  it('never returns something the canvas would ignore', () => {
    expect(fontOf({ fontStyle: '', fontWeight: '', fontSize: '', fontFamily: '' })).toBe('normal 400 16px sans-serif')
  })
})
