import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { api } from '@/api/client'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { Reveal } from '@/design/Reveal'
import { PosterSkeleton } from './PosterSkeleton'
import { useScreenView } from './view'
import { episodeLabel, countText, lengthText, missingText, seasonSummary, type SeriesCard, type SeriesDetail } from './series'

const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/** The TV shows in the library, shown inside Movies & Shows: a poster for each, with how many episodes are here. */
export function SeriesList() {
  const q = useQuery({ queryKey: ['series'], queryFn: () => api<{ items: SeriesCard[] }>('/api/screen/series') })
  const items = q.data?.items ?? []
  return (
    <div>
      <p className="mb-4 text-sm text-muted">Your TV shows, season by season</p>
      {q.isError ? <p role="alert" className="py-8 text-center text-sm text-warn">Could not load the shows.</p>
        : <Reveal ready={!q.isPending} skeleton={<PosterSkeleton label="Loading shows" />}>{!q.data ? null : !items.length ? <Empty icon="library" title="No shows yet">Add a folder of TV shows in Settings → Local files (marked SFW), then identify it in Settings → Metadata.</Empty>
        : (
          <ul aria-label="Shows" className="m-0 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
            {items.map((s, i) => (
              <li key={s.id} className="card-in m-0" style={{ '--i': i } as React.CSSProperties}>
                <Link to="/series/$id" params={{ id: String(s.id) }} aria-label={`Open ${s.title}`} className="ax-lift group relative block aspect-[2/3] overflow-hidden rounded-[23px] border border-white/10 bg-surface-2">
                  <img src={s.poster || PLACEHOLDER} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
                  <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.9)_60%)] p-3 pt-10">
                    <span className="truncate text-sm font-bold text-white" title={s.title}>{s.title}</span>
                    <span className="text-xs text-white/80">{s.year ? `${s.year} · ` : ''}{countText(s.episodes, 'episode')}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}</Reveal>}
    </div>
  )
}

/** One show, laid out like a streaming app: the artwork fills the page, the story is on the left, and the seasons and their episodes are in a panel on the right. */
export function SeriesDetailPage({ id }: { id: number }) {
  const q = useQuery({ queryKey: ['series', id], queryFn: () => api<SeriesDetail>(`/api/screen/series/${id}`) })
  const d = q.data
  const [picked, setPicked] = useState<number | null>(null)
  const [find, setFind] = useState('')
  const season = d?.seasons.find((s) => s.season === picked) ?? d?.seasons.find((s) => s.episodes.length) ?? d?.seasons[0]
  const word = find.trim().toLowerCase()
  const episodes = (season?.episodes ?? []).filter((e) => !word || e.name.toLowerCase().includes(word))
  const first = d?.seasons.flatMap((s) => s.episodes)[0]
  return (
    <div className="relative isolate min-h-[calc(100dvh-64px)] overflow-hidden">
      {d ? (
        <>
          <img src={d.backdrop || d.poster || PLACEHOLDER} alt="" aria-hidden="true" className="absolute inset-0 -z-20 size-full object-cover object-[70%_20%]" />
          <div aria-hidden="true" className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,color-mix(in_srgb,var(--color-bg)_96%,transparent)_0%,color-mix(in_srgb,var(--color-bg)_70%,transparent)_38%,color-mix(in_srgb,var(--color-bg)_12%,transparent)_72%),linear-gradient(0deg,color-mix(in_srgb,var(--color-bg)_95%,transparent)_0%,transparent_45%)]" />
        </>
      ) : null}
      <div className="grid gap-6 px-4 pb-8 pt-4 sm:px-10 lg:grid-cols-[minmax(0,1fr)_minmax(340px,420px)] lg:gap-10">
        <div className="flex min-h-[60vh] flex-col lg:min-h-[calc(100dvh-110px)]">
          <Link to="/videos" onClick={() => useScreenView.getState().setView('series')} aria-label="Back to series" className="grid size-10 place-items-center self-start rounded-full bg-fg/10 text-fg backdrop-blur hover:bg-fg/20"><Icon name="arrow-left" className="size-5" /></Link>
          {q.isPending ? <p role="status" className="py-8 text-sm text-muted">Loading…</p>
            : q.isError || !d ? <p role="alert" className="py-8 text-sm text-warn">This show is not in the library.</p>
            : (
              <div className="mt-auto grid max-w-2xl gap-4 pt-16">
                <h1 className="text-[clamp(34px,5vw,64px)] font-black leading-[0.95] tracking-[-0.04em] drop-shadow-lg">{d.title}</h1>
                {d.tagline ? <p className="text-sm italic text-fg/70">{d.tagline}</p> : null}
                <p aria-label="Facts" className="m-0 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-semibold">
                  {d.year ? <span>{d.year}{d.status === 'Returning Series' ? '–' : ''}</span> : null}
                  {d.seasons.length ? <span>{countText(d.seasons.filter((s) => s.season > 0).length || d.seasons.length, 'season')}</span> : null}
                  {d.rating ? <span className="inline-flex items-center gap-1.5">{d.rating.toFixed(1)}<span className="rounded bg-amber-400 px-1 text-[11px] font-black text-black">RATING</span></span> : null}
                </p>
                <p className="max-w-xl text-[15px] leading-relaxed text-fg/80">{d.overview}</p>
                {d.genres.length ? <div className="flex flex-wrap items-center gap-2"><span className="w-16 text-[11px] font-bold uppercase tracking-wide text-faint">Genres</span><ul aria-label="Genres" className="m-0 flex list-none flex-wrap gap-2 p-0">{d.genres.map((g) => <li key={g} className="rounded-full bg-fg/10 px-3.5 py-1.5 text-[13px] font-semibold backdrop-blur">{g}</li>)}</ul></div> : null}
                {d.cast.length ? <div className="flex flex-wrap items-center gap-2"><span className="w-16 text-[11px] font-bold uppercase tracking-wide text-faint">Cast</span><ul aria-label="Cast" className="m-0 flex list-none flex-wrap gap-2 p-0">{d.cast.slice(0, 6).map((c) => <li key={c.name} title={c.character} className="rounded-full bg-fg/10 px-3.5 py-1.5 text-[13px] font-semibold backdrop-blur">{c.name}</li>)}</ul></div> : null}
                {d.companies.length ? <p className="text-xs text-faint">{d.companies.join(' · ')}</p> : null}
                {first ? (
                  <div className="mt-2 flex flex-wrap gap-3">
                    <Link to="/watch/$cid/$mid" params={{ cid: first.cid, mid: String(first.mid) }} className="inline-flex h-12 items-center gap-2 rounded-full bg-accent px-6 text-[15px] font-bold text-accent-fg hover:brightness-110"><Icon name="play" className="size-4" />Play {episodeLabel(d.seasons.find((s) => s.episodes.includes(first))?.season ?? 1, first) || 'first episode'}</Link>
                  </div>
                ) : null}
              </div>
            )}
        </div>
        {d ? (
          <aside aria-label="Seasons and episodes" className="self-start rounded-[26px] border border-line bg-surface/80 p-4 shadow-2xl backdrop-blur-xl lg:sticky lg:top-20 lg:max-h-[calc(100dvh-100px)] lg:overflow-y-auto">
            {d.seasons.length ? (
              <>
                <div role="tablist" aria-label="Seasons" className="mb-3 flex gap-1 overflow-x-auto rounded-full bg-fg/10 p-1">
                  {d.seasons.map((s) => (
                    <button key={s.season} type="button" role="tab" aria-selected={s.season === season?.season} onClick={() => { setPicked(s.season); setFind('') }}
                      className={cn('shrink-0 rounded-full px-4 py-1.5 text-sm font-semibold transition-colors', s.season === season?.season ? 'bg-fg text-bg' : 'text-fg/80 hover:text-fg')}>{s.season === 0 ? 'Special' : s.name}</button>
                  ))}
                </div>
                {season ? <p className="mb-2 px-1 text-xs font-medium text-faint">{seasonSummary(season)}</p> : null}
                {season?.missing?.length ? <p className="mb-2 px-1 text-xs text-warn" aria-label={`Missing from ${season.name}`}>{season.episodes.length ? `Missing: ${missingText(season.missing)}` : `None of this season is here (${season.missing.length.toLocaleString()} episodes)`}</p> : null}
                <label className="mb-3 flex h-11 items-center gap-2 rounded-full bg-fg/10 px-4 text-sm"><Icon name="search" className="size-4 text-muted" /><span className="sr-only">Search episodes</span><input value={find} onChange={(e) => setFind(e.target.value)} placeholder="search videos" className="w-full bg-transparent outline-none placeholder:text-muted" /></label>
                <ul className="m-0 grid list-none gap-1 p-0">
                  {episodes.map((e) => (
                    <li key={`${e.cid}:${e.mid}`} className="m-0">
                      <Link to="/watch/$cid/$mid" params={{ cid: e.cid, mid: String(e.mid) }} className="flex items-center gap-3 rounded-2xl p-2 hover:bg-fg/10 focus-visible:bg-fg/10">
                        <img src={e.still || d.poster || PLACEHOLDER} alt="" loading="lazy" className="aspect-video w-32 shrink-0 rounded-lg bg-surface-3 object-cover" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[15px] font-bold">{e.episode != null ? `${e.episode}. ` : ''}{e.name}</span>
                          <span className="block truncate text-xs text-muted">{[episodeLabel(season?.season ?? 1, e), e.air_date, lengthText(e)].filter(Boolean).join(' · ')}</span>
                        </span>
                        <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full border border-line-2"><Icon name="play" className="size-3.5" /></span>
                      </Link>
                    </li>
                  ))}
                  {!episodes.length && season?.episodes.length ? <li className="px-2 py-4 text-sm text-muted">No episode matches “{find}”.</li> : null}
                </ul>
              </>
            ) : <Empty icon="library" title="No episodes in the library">Episodes appear here once their files are in an SFW folder and identified.</Empty>}
          </aside>
        ) : null}
      </div>
    </div>
  )
}
