/** Movies and TV (the SFW side): what the screen routes answer, and the words used for them. */
export type Provider = 'tmdb' | 'omdb' | 'opensubtitles'

export interface ScreenSettings {
  tmdb_api_key: string; omdb_api_key: string; opensubtitles_api_key: string
  tmdb_configured: boolean; omdb_configured: boolean; opensubtitles_configured: boolean
}
export interface ScreenStatus {
  progress: { running?: boolean; total?: number; done?: number; matched?: number; review?: number; unmatched?: number; failed?: number }
  counts: Record<string, number>; pending: number; configured: boolean
}
export interface Candidate { kind: 'movie' | 'tv'; tmdb_id: number; title: string; year: number | null; score: number; source: string }
export interface ReviewItem {
  id: number; filename: string; title: string; state: 'REVIEW' | 'UNMATCHED'; season: number | null; episode: number | null
  candidates: Candidate[]; parsed: { kind: 'movie' | 'episode'; title: string; year: number | null; season: number | null; episode: number | null }
}

export const PROVIDERS: { id: Provider; field: keyof ScreenSettings; label: string; help: string }[] = [
  { id: 'tmdb', field: 'tmdb_api_key', label: 'TMDB — API key or read token', help: 'Names, posters, cast and episodes. Free at themoviedb.org (Settings → API).' },
  { id: 'opensubtitles', field: 'opensubtitles_api_key', label: 'OpenSubtitles — API key', help: 'Recognises a file exactly, by its hash. Free account at opensubtitles.com.' },
  { id: 'omdb', field: 'omdb_api_key', label: 'OMDb — API key (optional)', help: 'A second opinion when TMDB finds nothing. Free at omdbapi.com.' },
]

export const configuredOf = (s: ScreenSettings | undefined, p: Provider) => Boolean(s?.[`${p}_configured` as keyof ScreenSettings])

export function statusText(s: ScreenStatus | undefined): string {
  if (!s) return ''
  if (!s.configured) return 'Add a TMDB key to name your movies and shows.'
  const p = s.progress
  if (p.running) return `Identifying: ${(p.done ?? 0).toLocaleString()} of ${(p.total ?? 0).toLocaleString()}`
  const m = s.counts.MATCHED ?? 0
  const bits = [`${m.toLocaleString()} identified`]
  if (s.counts.REVIEW) bits.push(`${s.counts.REVIEW.toLocaleString()} to check`)
  if (s.counts.UNMATCHED) bits.push(`${s.counts.UNMATCHED.toLocaleString()} not found`)
  if (s.pending) bits.push(`${s.pending.toLocaleString()} waiting`)
  return bits.join(' · ')
}

export const candidateText = (c: Candidate) => `${c.title}${c.year ? ` (${c.year})` : ''} · ${c.kind === 'tv' ? 'TV show' : 'Movie'}`
