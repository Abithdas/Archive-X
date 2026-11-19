export interface SeriesCard extends Partial<Facets> { id: number; title: string; year: number | null; poster: string; overview: string; genres: string[]; episodes: number; seasons: number }
import type { Facets } from './listFilters'
export interface EpisodeRow { episode: number | null; name: string; overview: string; air_date: string; still: string; runtime: number; duration: number; cid: string; mid: number }
export interface SeasonBlock { season: number; name: string; total: number; episodes: EpisodeRow[]; missing?: number[] }
export interface SeriesDetail {
  id: number; title: string; year: number | null; overview: string; tagline: string; poster: string; backdrop: string; genres: string[]; companies: string[]
  cast: { name: string; character: string; profile: string }[]; rating: number; status: string; seasons: SeasonBlock[]
}

export const countText = (n: number, one: string) => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`

/** "S02E03" or "Special 4"; an episode with no number is just listed. */
export function episodeLabel(season: number, e: Pick<EpisodeRow, 'episode'>): string {
  if (e.episode == null) return ''
  return season === 0 ? `Special ${e.episode}` : `S${String(season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`
}

/** How long, in the person's terms: the file's own length first, the provider's runtime otherwise. */
export function lengthText(e: Pick<EpisodeRow, 'duration' | 'runtime'>): string {
  const minutes = Math.round((e.duration > 0 ? e.duration / 60 : e.runtime) || 0)
  return minutes ? `${minutes} min` : ''
}

export function seasonSummary(s: SeasonBlock): string {
  return s.total && s.total > s.episodes.length ? `${s.episodes.length} of ${s.total} episodes here` : countText(s.episodes.length, 'episode')
}

/** Episode numbers as runs: [1, 2, 5, 6, 7] reads "E01–E02, E05–E07". */
export function missingText(missing: readonly number[]): string {
  const runs: string[] = []
  const e = (n: number) => `E${String(n).padStart(2, '0')}`
  for (let i = 0; i < missing.length;) {
    let j = i
    while (j + 1 < missing.length && missing[j + 1] === (missing[j] ?? 0) + 1) j++
    runs.push(j > i ? `${e(missing[i] ?? 0)}–${e(missing[j] ?? 0)}` : e(missing[i] ?? 0))
    i = j + 1
  }
  return runs.join(', ')
}

export interface MovieCard extends Partial<Facets> { id: number; title: string; year: number | null; poster: string; overview: string; genres: string[]; rating: number; release_date: string; added: number; versions: number; best: string; cam_only: boolean }
export interface MovieVersion { cid: string; mid: number; media_id: number; filename: string; resolution: string; height: number; source: string; cam: boolean; codec: string; size: number; duration: number; hdr?: string[]; languages?: string[] }
export interface MovieDetail {
  id: number; title: string; year: number | null; overview: string; tagline: string; poster: string; backdrop: string; genres: string[]; companies: string[]; rating: number; runtime: number
  cast: { name: string; character: string; profile: string }[]; versions: MovieVersion[]
}

export const MOVIE_SORTS = [{ value: 'title', label: 'A to Z' }, { value: 'recent', label: 'Recently added' }, { value: 'year', label: 'Newest release' }, { value: 'rating', label: 'Top rated' }] as const
export type MovieSort = (typeof MOVIE_SORTS)[number]['value']

export function sortMovies(items: readonly MovieCard[], sort: MovieSort, find: string): MovieCard[] {
  const text = find.trim().toLowerCase()
  const shown = text ? items.filter((m) => m.title.toLowerCase().includes(text)) : [...items]
  const by: Record<MovieSort, (a: MovieCard, b: MovieCard) => number> = {
    title: (a, b) => a.title.localeCompare(b.title),
    recent: (a, b) => b.added - a.added,
    year: (a, b) => (b.year ?? 0) - (a.year ?? 0) || a.title.localeCompare(b.title),
    rating: (a, b) => b.rating - a.rating || a.title.localeCompare(b.title),
  }
  return shown.sort(by[sort])
}

/** "1080p · 2 copies", "720p", or "Cinema recording" when that is all there is. */
export function movieBadge(m: Pick<MovieCard, 'best' | 'versions' | 'cam_only'>): string {
  if (m.cam_only) return 'Cinema recording'
  return [m.best, m.versions > 1 ? `${m.versions} copies` : ''].filter(Boolean).join(' · ')
}

export function sizeText(bytes: number): string {
  if (!bytes) return ''
  const gb = bytes / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`
}

export const COPY_SORTS = [{ value: 'best', label: 'Best first' }, { value: 'big', label: 'Largest file' }, { value: 'small', label: 'Smallest file' }] as const
export type CopySort = (typeof COPY_SORTS)[number]['value']
export function sortCopies<T extends Pick<MovieVersion, 'size'>>(copies: readonly T[], sort: CopySort): T[] {
  const out = [...copies]
  if (sort === 'big') out.sort((a, b) => b.size - a.size)
  else if (sort === 'small') out.sort((a, b) => a.size - b.size)
  return out   // 'best' keeps the server's order: best picture first, a cinema recording last
}
/** The big badge of a copy: "4K" over "HDR" or "Dolby Vision" (or its picture size and source when it has no HDR). */
export function copyBadge(v: Pick<MovieVersion, 'resolution' | 'height' | 'hdr' | 'source' | 'cam'>): { top: string; bottom: string } {
  const top = v.cam ? 'CAM' : v.height >= 2000 ? '4K' : v.resolution || '?'
  const hdr = v.hdr ?? []
  return { top, bottom: hdr.includes('DV') ? 'Dolby Vision' : hdr.includes('HDR') ? 'HDR' : v.source }
}
export const versionText = (v: Pick<MovieVersion, 'resolution' | 'source' | 'codec' | 'size' | 'cam'>) =>
  [v.cam ? 'Cinema recording' : '', v.resolution, v.source, v.codec.toUpperCase(), sizeText(v.size)].filter(Boolean).join(' · ')
