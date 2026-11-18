export interface SeriesCard { id: number; title: string; year: number | null; poster: string; overview: string; genres: string[]; episodes: number; seasons: number }
export interface EpisodeRow { episode: number | null; name: string; overview: string; air_date: string; still: string; runtime: number; duration: number; cid: string; mid: number }
export interface SeasonBlock { season: number; name: string; total: number; episodes: EpisodeRow[] }
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
