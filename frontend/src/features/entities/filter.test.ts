import { describe, expect, it } from 'vitest'
import { nameMatches } from './filter'


describe('nameMatches', () => {
  it('finds a name from part of it, in any word order, without accents or spaces', () => {
    for (const q of ['ava st', 'stone ava', 'AVA', 'avastone', 'ava, stone']) expect(nameMatches('Ava Stone', q)).toBe(true)
    expect(nameMatches('Zoë Ray', 'zoe')).toBe(true)
    expect(nameMatches('Ava Stone', 'ava ray')).toBe(false)
    expect(nameMatches('Ava Stone', '')).toBe(true)
  })
})
