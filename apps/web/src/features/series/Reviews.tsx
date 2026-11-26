import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'

export interface ReviewsData { ok: boolean; scores: Array<{ source: string; value: string }>; summary: { text: string; title: string; url: string } | null }

/** Critics' scores and what critics said (Wikidata and Wikipedia: no key, only the IMDb number is sent). Nothing is shown while it loads or when it has nothing. */
export const useReviews = (kind: 'movie' | 'series', ident: string) =>
  useQuery({ queryKey: ['reviews', kind, ident] as const, queryFn: () => api<ReviewsData>(`/api/screen/reviews/${kind}/${ident}`), enabled: /^tt\d{5,10}$/.test(ident), staleTime: 30 * 60_000, retry: false })

const SHORT: Record<string, string> = { 'Rotten Tomatoes': 'RT', Metacritic: 'MC' }
/** Metacritic's own colours: green from 61, yellow from 40, red below. Rotten Tomatoes' percentage is "fresh" from 60. */
export function scoreTone(source: string, value: string): 'good' | 'mixed' | 'bad' {
  const n = parseInt(value, 10)
  if (!Number.isFinite(n)) return 'mixed'
  if (source === 'Metacritic') return n >= 61 ? 'good' : n >= 40 ? 'mixed' : 'bad'
  return n >= 60 ? 'good' : n >= 40 ? 'mixed' : 'bad'
}
const TONE = { good: 'bg-emerald-500/20 text-emerald-300', mixed: 'bg-amber-400/20 text-amber-300', bad: 'bg-red-500/20 text-red-300' } as const

/** The critics' scores as small marks beside the IMDb rating. */
export function ScoreBadges({ data }: { data: ReviewsData | undefined }) {
  const scores = data?.scores ?? []
  if (!scores.length) return null
  return (
    <>
      {scores.map((s) => (
        <span key={s.source} title={`${s.source}: ${s.value}`} aria-label={`${s.source} ${s.value}`} className={cn('inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[12px] font-black', TONE[scoreTone(s.source, s.value)])}>
          <span aria-hidden="true" className="text-[10px] font-bold opacity-80">{SHORT[s.source] ?? s.source}</span>{s.value}
        </span>
      ))}
    </>
  )
}

/** A "Reviews" button beside Trailer: the scores and the opening of what critics said, with a link to read the rest. Not shown when there is nothing to show. */
export function ReviewsButton({ data, title, className }: { data: ReviewsData | undefined; title: string; className: string }) {
  const [open, setOpen] = useState(false)
  const scores = data?.scores ?? []
  if (!data || (!scores.length && !data.summary)) return null
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={className}><Icon name="sparkle" className="mr-1.5 size-3.5" />Reviews</button>
      {open ? (
        <Dialog open onClose={() => setOpen(false)} title={`${title}: reviews`}>
          <div className="grid gap-4">
            {scores.length ? (
              <ul aria-label="Critics' scores" className="m-0 flex list-none flex-wrap gap-2 p-0">
                {scores.map((s) => <li key={s.source} className={cn('m-0 grid min-w-28 gap-0.5 rounded-xl px-4 py-2.5', TONE[scoreTone(s.source, s.value)])}><span className="text-2xl font-black leading-none">{s.value}</span><span className="text-[11px] font-semibold opacity-80">{s.source}</span></li>)}
              </ul>
            ) : null}
            {data.summary ? (
              <section aria-label="What critics said" className="grid gap-2">
                <h3 className="m-0 text-sm font-bold">What critics said</h3>
                <p className="m-0 text-[14px] leading-relaxed text-fg/85">{data.summary.text}</p>
                <a href={data.summary.url} target="_blank" rel="noreferrer" className="w-fit text-[13px] font-semibold text-accent hover:underline">Read more in “{data.summary.title}” on Wikipedia ↗</a>
              </section>
            ) : null}
            <p className="m-0 text-[11px] text-faint">Scores come from Wikidata and the summary from Wikipedia. They can be a little behind the sites themselves.</p>
          </div>
        </Dialog>
      ) : null}
    </>
  )
}
