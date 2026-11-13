import { describe, expect, it } from 'vitest'
import { entitiesUrl, lookupUrl, uniqueNames } from './hooks'

describe('catalogue requests stay small', () => {
  it('the directory list asks for the brief fields only', () => { expect(entitiesUrl('performers')).toBe('/api/performers?limit=5000&brief=1') })
  it('detail pages look up only the names they know, once each, sorted', () => {
    expect(uniqueNames(['b', ' a ', 'b', '', null, undefined, 'a'])).toEqual(['a', 'b'])
    expect(lookupUrl('studios', ['A', 'B'])).toBe('/api/entity-lookup?kind=studios&names=' + encodeURIComponent('["A","B"]'))
    expect(uniqueNames(Array.from({ length: 400 }, (_, i) => `n${i}`)).length).toBe(300)
  })
})
