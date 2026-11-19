/** The filters of the Movies and Series lists: picture quality, release source, HDR or Dolby Vision, and language. A title matches when any of its copies does. */
export interface Facets { res: number[]; sources: string[]; hdr: string[]; langs: string[] }
export interface ListFilter { quality: string; source: string; hdr: string; lang: string }   // '' means any
export const NO_FILTER: ListFilter = { quality: '', source: '', hdr: '', lang: '' }

export const QUALITIES = [['4k', '4K'], ['1080', '1080p'], ['720', '720p'], ['sd', 'SD']] as const
export const HDRS = [['hdr', 'HDR'], ['dv', 'Dolby Vision'], ['sdr', 'No HDR']] as const

const hasQuality = (res: readonly number[], q: string) => (q === '4k' ? res.some((h) => h >= 2000) : q === '1080' ? res.includes(1080) : q === '720' ? res.includes(720) : res.some((h) => h > 0 && h < 720))

export function matchesFilter(f: ListFilter, x: Partial<Facets>): boolean {
  const res = x.res ?? []
  const hdr = x.hdr ?? []
  if (f.quality && !hasQuality(res, f.quality)) return false
  if (f.source && !(x.sources ?? []).includes(f.source)) return false
  if (f.hdr === 'hdr' && !hdr.includes('HDR')) return false
  if (f.hdr === 'dv' && !hdr.includes('DV')) return false
  if (f.hdr === 'sdr' && hdr.length) return false
  if (f.lang && !(x.langs ?? []).includes(f.lang)) return false
  return true
}
export const filterCount = (f: ListFilter) => [f.quality, f.source, f.hdr, f.lang].filter(Boolean).length

/** Only the choices that exist in this library are offered: a source or a language nobody has is not listed. */
export function optionsOf(items: ReadonlyArray<Partial<Facets>>) {
  const sources = new Set<string>(), langs = new Set<string>(), res = new Set<number>()
  let hdr = false, dv = false, plain = false
  for (const x of items) {
    (x.sources ?? []).forEach((s) => sources.add(s)); (x.langs ?? []).forEach((l) => langs.add(l)); (x.res ?? []).forEach((r) => res.add(r))
    const h = x.hdr ?? []
    if (h.includes('HDR')) hdr = true
    if (h.includes('DV')) dv = true
    if (!h.length) plain = true
  }
  const all = [...res]
  return {
    qualities: QUALITIES.filter(([k]) => hasQuality(all, k)),
    sources: [...sources].sort(), langs: [...langs].sort(),
    hdrs: HDRS.filter(([k]) => (k === 'hdr' ? hdr : k === 'dv' ? dv : plain && (hdr || dv))),
  }
}
