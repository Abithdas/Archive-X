import { describe, expect, it } from 'vitest'
import { COUNTRIES, DEFAULT_COUNTRY, composePhone, filterCountries, matchDial, splitSaved } from './countries'

const find = (iso: string) => COUNTRIES.find((c) => c.iso === iso)!
describe('countries', () => {
  it('keeps the classic list', () => { expect(COUNTRIES).toHaveLength(61); expect(DEFAULT_COUNTRY).toEqual({ iso: 'IN', name: 'India', dial: '91' }); expect(COUNTRIES[1]!.iso).toBe('US') })
  it('searches by name, code or dial', () => {
    expect(filterCountries('')).toHaveLength(61); expect(filterCountries('unit').map((c) => c.iso)).toEqual(['US', 'GB', 'AE']); expect(filterCountries('de').some((c) => c.iso === 'DE')).toBe(true)
    expect(filterCountries('+44').map((c) => c.iso)).toContain('GB'); expect(filterCountries('zzz')).toEqual([])
  })
  it('matches the longest calling code', () => { expect(matchDial('+971501234567')?.iso).toBe('AE'); expect(matchDial('9112345')?.iso).toBe('IN'); expect(matchDial('1415')?.dial).toBe('1'); expect(matchDial('999')).toBeNull() })
  it('composes the full number', () => {
    expect(composePhone('98765 43210', DEFAULT_COUNTRY)).toEqual({ full: '+919876543210', country: DEFAULT_COUNTRY, local: '9876543210' })
    expect(composePhone('', DEFAULT_COUNTRY).full).toBe(''); expect(composePhone('(415) 555-0123', find('US')).full).toBe('+14155550123')
    const pasted = composePhone('+44 20 7946 0958', DEFAULT_COUNTRY); expect(pasted.country.iso).toBe('GB'); expect(pasted.full).toBe('+442079460958'); expect(pasted.local).toBe('2079460958')
    expect(composePhone('0044 20 7946 0958', DEFAULT_COUNTRY).full).toBe('+442079460958')
    expect(composePhone('+91', DEFAULT_COUNTRY).full).toBe('')   // only a code: nothing to split
  })
  it('restores a saved number', () => { expect(splitSaved('+442079460958')).toMatchObject({ country: { iso: 'GB' }, local: '2079460958' }); expect(splitSaved('')).toEqual({ country: DEFAULT_COUNTRY, local: '' }); expect(splitSaved('+999123').country).toEqual(DEFAULT_COUNTRY) })
})
