import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { api } from '@/api/client'
import type { LibraryItem } from '@/api/types'
import { countText } from './series'

interface TitleOfMedia { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; videos: number }
const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/**
 * Movies and shows: the videos of a cast member or a studio as one poster for each movie and each show (never one card for each episode or copy),
 * followed by the videos that have no title yet, as ordinary cards (`loose`).
 */
export function TitleCards({ videos, loose }: { videos: readonly LibraryItem[]; loose: (items: LibraryItem[]) => ReactNode }) {
  const ids = videos.map((v) => v.media_id).filter((n): n is number => Number.isFinite(n))
  const q = useQuery({ queryKey: ['titles-of-media', ids.join(',')] as const, queryFn: () => api<{ titles: TitleOfMedia[]; loose: number[] }>(`/api/screen/of-media?ids=${ids.join(',')}`), enabled: ids.length > 0, staleTime: 30_000 })
  if (q.isPending && ids.length) return <p role="status" className="py-6 text-sm text-muted">Gathering the movies and shows…</p>
  const titles = q.data?.titles ?? []
  const left = q.data ? new Set(q.data.loose) : new Set(ids)
  const rest = videos.filter((v) => left.has(v.media_id as number))
  return (
    <div className="grid gap-6">
      {titles.length ? (
        <ul aria-label="Movies and shows" className="m-0 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
          {titles.map((t) => (
            <li key={`${t.kind}:${t.id}`} className="m-0">
              <Link to={t.kind === 'tv' ? '/series/$id' : '/movies/$id'} params={{ id: String(t.id) }} aria-label={`Open ${t.title}`} className="ax-lift group relative block aspect-[2/3] overflow-hidden rounded-[23px] border border-white/10 bg-surface-2">
                <img src={t.poster || PLACEHOLDER} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
                <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.9)_60%)] p-3 pt-10">
                  <span className="truncate text-sm font-bold text-white" title={t.title}>{t.title}</span>
                  <span className="text-xs text-white/80">{t.year ? `${t.year} · ` : ''}{t.kind === 'tv' ? countText(t.videos, 'episode') : 'Movie'}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      {rest.length ? loose(rest) : null}
    </div>
  )
}
