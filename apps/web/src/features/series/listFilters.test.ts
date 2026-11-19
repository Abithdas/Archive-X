import { describe, expect, it } from 'vitest'
import { NO_FILTER, filterCount, matchesFilter, optionsOf } from './listFilters'

const uhd = { res: [2160, 1080], sources: ['BluRay'], hdr: ['HDR', 'DV'], langs: ['German'] }
const web = { res: [720], sources: ['WEB-DL'], hdr: [], langs: [] }

describe('Movies and Series filters', () => {
  it('lets everything through when nothing is chosen', () => { expect(matchesFilter(NO_FILTER, uhd)).toBe(true); expect(matchesFilter(NO_FILTER, {})).toBe(true) })
  it('matches a title when any of its copies has the quality', () => {
    expect(matchesFilter({ ...NO_FILTER, quality: '1080' }, uhd)).toBe(true)        // it also has a 4K copy
    expect(matchesFilter({ ...NO_FILTER, quality: '4k' }, web)).toBe(false)
    expect(matchesFilter({ ...NO_FILTER, quality: 'sd' }, web)).toBe(false)         // 720p is not SD
    expect(matchesFilter({ ...NO_FILTER, quality: 'sd' }, { res: [480] })).toBe(true)
  })
  it('filters by source, HDR, Dolby Vision and language, all at once', () => {
    expect(matchesFilter({ ...NO_FILTER, source: 'BluRay', hdr: 'dv', lang: 'German' }, uhd)).toBe(true)
    expect(matchesFilter({ ...NO_FILTER, hdr: 'hdr' }, web)).toBe(false)
    expect(matchesFilter({ ...NO_FILTER, hdr: 'sdr' }, web)).toBe(true); expect(matchesFilter({ ...NO_FILTER, hdr: 'sdr' }, uhd)).toBe(false)
    expect(matchesFilter({ ...NO_FILTER, source: 'WEB-DL', lang: 'German' }, web)).toBe(false)
  })
  it('counts what is chosen', () => expect(filterCount({ quality: '4k', source: '', hdr: 'hdr', lang: '' })).toBe(2))
  it('offers only what the library has', () => {
    const o = optionsOf([uhd, web])
    expect(o.qualities.map(([k]) => k)).toEqual(['4k', '1080', '720']); expect(o.sources).toEqual(['BluRay', 'WEB-DL']); expect(o.langs).toEqual(['German'])
    expect(o.hdrs.map(([k]) => k)).toEqual(['hdr', 'dv', 'sdr'])
    expect(optionsOf([web]).hdrs).toEqual([])
  })
})
