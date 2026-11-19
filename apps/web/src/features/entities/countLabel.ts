import type { Entity } from '@/api/types'

const plural = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`

/** The count on a card: "3 videos", or in SFW "2 movies · 1 series" (titles, not files); "1 video" style when no title is known yet. */
export function countLabel(e: Pick<Entity, 'video_count' | 'movies' | 'series'>, titles: boolean): string {
  const movies = e.movies ?? 0
  const series = e.series ?? 0
  if (titles && movies + series > 0) return [movies ? plural(movies, 'movie', 'movies') : '', series ? plural(series, 'series', 'series') : ''].filter(Boolean).join(' · ')
  return plural(e.video_count, 'video', 'videos')
}
