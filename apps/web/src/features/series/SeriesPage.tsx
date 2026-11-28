import { useSearch } from '@/app/search'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { playClick } from '@/features/watch/fullscreen'
import { api } from '@/api/client'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { Reveal } from '@/design/Reveal'
import { PLACEHOLDER, PosterCard } from './PosterCard'
import { PosterSkeleton } from './PosterSkeleton'
import { Chips, TitleStage } from './TitleStage'
import { useScreenView } from './view'
import { ListFilters } from './FilterPanel'
import { NO_FILTER, filterCount, matchesFilter, type ListFilter } from './listFilters'
import { episodeLabel, countText, lengthText, missingText, seasonSummary, type SeriesCard, type SeriesDetail } from './series'
import { ReviewsButton, ScoreBadges, useReviews } from './Reviews'
import { useTitle } from './DiscoverPages'
import { KeepButton } from '@/features/collection/KeepButton'


/** The TV shows in the library, shown inside Movies & Shows: a poster for each, with how many episodes are here. */
const SERIES_SORTS = [{ value: 'title', label: 'A to Z' }, { value: 'year', label: 'Newest release' }, { value: 'episodes', label: 'Most episodes' }] as const
type SeriesSort = (typeof SERIES_SORTS)[number]['value']

export function SeriesList() {
  const q = useQuery({ queryKey: ['series'], queryFn: () => api<{ items: SeriesCard[] }>('/api/screen/series') })
  const [sort, setSort] = useState<SeriesSort>('title')
  const find = useSearch((x) => x.q)   // the top bar's search is the one search box (the library list is narrowed by it)
  const [filter, setFilter] = useState<ListFilter>(NO_FILTER)
  const all = q.data?.items ?? []
  const text = find.trim().toLowerCase()
  const by: Record<SeriesSort, (a: SeriesCard, b: SeriesCard) => number> = { title: (a, b) => a.title.localeCompare(b.title), year: (a, b) => (b.year ?? 0) - (a.year ?? 0) || a.title.localeCompare(b.title), episodes: (a, b) => b.episodes - a.episodes || a.title.localeCompare(b.title) }
  const items = all.filter((x) => (!text || x.title.toLowerCase().includes(text)) && matchesFilter(filter, x)).sort(by[sort])
  const narrowed = Boolean(text) || filterCount(filter) > 0
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted">Your TV shows, season by season{q.data ? <span className="ml-2 text-faint">· {narrowed ? `${items.length.toLocaleString()} of ${all.length.toLocaleString()}` : all.length.toLocaleString()} shows</span> : null}</p>
        <div className="flex flex-wrap items-center gap-2">
          <ListFilters items={all} value={filter} onChange={setFilter} sort={{ value: sort, options: SERIES_SORTS, onChange: (v) => setSort(v as SeriesSort) }} />
        </div>
      </div>
      {q.isError ? <p role="alert" className="py-8 text-center text-sm text-warn">Could not load the shows.</p>
        : <Reveal ready={!q.isPending} skeleton={<PosterSkeleton label="Loading shows" />}>{!q.data ? null : !items.length ? <Empty icon="library" title={narrowed ? 'No show matches' : 'No shows yet'}>{narrowed ? 'Try other words, or clear the filters.' : 'Add a folder of TV shows in Settings → Local files (marked SFW), then identify it in Settings → Metadata.'}</Empty>
        : (
          <ul aria-label="Shows" className="m-0 grid list-none grid-cols-2 gap-x-4 gap-y-5 p-0 sm:grid-cols-[repeat(auto-fill,minmax(170px,1fr))]">
            {items.map((x, i) => (
              <li key={x.id} className="card-in m-0" style={{ '--i': i } as React.CSSProperties}>
                <PosterCard to="/series/$id" params={{ id: String(x.id) }} image={x.poster} title={x.title} sub={`${x.year ? `${x.year} · ` : ''}${countText(x.episodes, 'episode')}`} />
              </li>
            ))}
          </ul>
        )}</Reveal>}
    </div>
  )
}

function GapBody({ d, f, n, season }: { d: SeriesDetail; f?: { name: string; released: string; thumb: string }; n: number; season: number }) {
  return (
    <>
      <span className="relative grid aspect-video w-32 shrink-0 place-items-center overflow-hidden rounded-lg bg-surface-3">
        {f?.thumb ? <img src={f.thumb} alt="" loading="lazy" className="size-full object-cover" /> : <img src={d.poster || PLACEHOLDER} alt="" loading="lazy" className="size-full object-cover opacity-40" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-bold">{n}. {f?.name || `Episode ${n}`}</span>
        <span className="block truncate text-xs text-muted">{[episodeLabel(season, { episode: n }), f?.released, 'Not in your library · find it in your add-ons'].filter(Boolean).join(' · ')}</span>
      </span>
    </>
  )
}

/** One show, laid out like a streaming app: the artwork fills the page, the story is on the left, and the seasons and their episodes are in a panel on the right. */
export function SeriesDetailPage({ id }: { id: number }) {
  const q = useQuery({ queryKey: ['series', id], queryFn: () => api<SeriesDetail>(`/api/screen/series/${id}`) })
  const d = q.data
  const reviews = useReviews('series', d?.imdb ?? '')
  const [picked, setPicked] = useState<number | null>(null)
  const [find, setFind] = useState('')
  const season = d?.seasons.find((s) => s.season === picked) ?? d?.seasons.find((s) => s.episodes.length) ?? d?.seasons[0]
  const word = find.trim().toLowerCase()
  const episodes = (season?.episodes ?? []).filter((e) => !word || e.name.toLowerCase().includes(word))
  // Episodes the library does not have: named by the catalogue, and opened on their own page, where the person's add-ons offer streams for them.
  const found = useTitle('series', d?.imdb ?? '')
  const foundSeason = found.data?.episodes?.find((x) => x.season === season?.season)?.episodes
  const gaps = (season?.missing ?? []).map((n) => ({ n, f: foundSeason?.find((x) => x.episode === n) })).filter((g) => !word || (g.f?.name ?? '').toLowerCase().includes(word))
  const first = d?.seasons.flatMap((s) => s.episodes)[0]
  const left = q.isPending ? <p role="status" className="py-8 text-sm text-muted">Loading…</p>
    : q.isError || !d ? <p role="alert" className="py-8 text-sm text-warn">This show is not in the library.</p>
    : (
      <div className="mo-enter mt-auto grid max-w-2xl gap-4 pt-16">
        <h1 className="text-[clamp(34px,5vw,64px)] font-black leading-[0.95] tracking-[-0.04em] drop-shadow-lg">{d.title}</h1>
        {d.tagline ? <p className="text-sm italic text-fg/70">{d.tagline}</p> : null}
        <p aria-label="Facts" className="m-0 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-semibold">
          {d.year ? <span>{d.year}{d.status === 'Returning Series' ? '–' : ''}</span> : null}
          {d.seasons.length ? <span>{countText(d.seasons.filter((s) => s.season > 0).length || d.seasons.length, 'season')}</span> : null}
          {d.rating ? <span className="inline-flex items-center gap-1.5">{d.rating.toFixed(1)}<span className="rounded bg-amber-400 px-1 text-[11px] font-black text-black">RATING</span></span> : null}
          <ScoreBadges data={reviews.data} />
        </p>
        <p className="max-w-xl text-[15px] leading-relaxed text-fg/80">{d.overview}</p>
        <Chips label="Genres" items={d.genres} />
        <Chips label="Cast" cast items={d.cast.slice(0, 6).map((c) => c.name)} title={(n) => d.cast.find((c) => c.name === n)?.character} />
        {d.companies.length ? <p className="text-xs text-faint">{d.companies.join(' · ')}</p> : null}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {first ? <Link to="/watch/$cid/$mid" onClick={playClick} params={{ cid: first.cid, mid: String(first.mid) }} className="inline-flex h-12 w-fit items-center gap-2 rounded-full bg-accent px-6 text-[15px] font-bold text-accent-fg transition-[filter,transform] hover:brightness-110 active:scale-[0.98]"><Icon name="play" className="size-4" />Play {episodeLabel(d.seasons.find((s) => s.episodes.includes(first))?.season ?? 1, first) || 'first episode'}</Link> : null}
          <ReviewsButton data={reviews.data} title={d.title} className="inline-flex h-12 items-center rounded-full border border-line-2 px-5 text-sm font-semibold transition-colors hover:bg-fg/10" />
          {d.imdb && d.seasons.some((s) => s.missing?.length) ? <KeepButton kind="series" imdb={d.imdb} title={d.title} year={d.year} poster={d.poster} label="Get the missing episodes" className="h-12" /> : null}
        </div>
      </div>
    )
  const panel = !d ? null : d.seasons.length ? (
    <>
      <div role="tablist" aria-label="Seasons" className="mb-3 flex gap-1 overflow-x-auto rounded-full bg-fg/10 p-1">
        {d.seasons.map((s) => (
          <button key={s.season} type="button" role="tab" aria-selected={s.season === season?.season} onClick={() => { setPicked(s.season); setFind('') }}
            className={cn('shrink-0 rounded-full px-4 py-1.5 text-sm font-semibold transition-[background-color,color] duration-300', s.season === season?.season ? 'bg-fg text-bg' : 'text-fg/80 hover:text-fg')}>{s.season === 0 ? 'Special' : s.name}</button>
        ))}
      </div>
      {season ? <p className="mb-2 px-1 text-xs font-medium text-faint">{seasonSummary(season)}</p> : null}
      {season?.missing?.length ? <p className="mb-2 px-1 text-xs text-warn" aria-label={`Missing from ${season.name}`}>{season.episodes.length ? `Missing: ${missingText(season.missing)}` : `None of this season is here (${season.missing.length.toLocaleString()} episodes)`}</p> : null}
      <label className="mb-3 flex h-11 items-center gap-2 rounded-full bg-fg/10 px-4 text-sm"><Icon name="search" className="size-4 text-muted" /><span className="sr-only">Search episodes</span><input value={find} onChange={(e) => setFind(e.target.value)} placeholder="search videos" className="w-full bg-transparent outline-none placeholder:text-muted" /></label>
      <ul key={season?.season} className="m-0 grid list-none gap-1 p-0">
        {episodes.map((e, n) => (
          <li key={`${e.cid}:${e.mid}`} className="mo-enter m-0" style={{ animationDelay: `${Math.min(n, 10) * 35}ms` }}>
            <Link to="/watch/$cid/$mid" onClick={playClick} params={{ cid: e.cid, mid: String(e.mid) }} className="group flex items-center gap-3 rounded-2xl p-2 transition-colors hover:bg-fg/10 focus-visible:bg-fg/10">
              <img src={e.still || d.poster || PLACEHOLDER} alt="" loading="lazy" className="aspect-video w-32 shrink-0 rounded-lg bg-surface-3 object-cover transition-transform duration-500 group-hover:scale-[1.03]" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px] font-bold">{e.episode != null ? `${e.episode}. ` : ''}{e.name}</span>
                <span className="block truncate text-xs text-muted">{[episodeLabel(season?.season ?? 1, e), e.air_date, lengthText(e)].filter(Boolean).join(' · ')}</span>
              </span>
              <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full border border-line-2 transition-colors group-hover:bg-fg group-hover:text-bg"><Icon name="play" className="size-3.5" /></span>
            </Link>
          </li>
        ))}
        {gaps.map(({ n, f }) => (
          <li key={`gap${n}`} className="m-0">
            {d?.imdb && /^(tt|tmdb-)/.test(d.imdb)
              ? <Link to="/discover/$kind/$id/$season/$episode" params={{ kind: 'series', id: d.imdb, season: String(season?.season ?? 1), episode: String(n) }} aria-label={`Episode ${n} is not in your library: find it in your add-ons`} className="group flex items-center gap-3 rounded-2xl p-2 opacity-80 transition-colors hover:bg-fg/10 hover:opacity-100 focus-visible:bg-fg/10">
                <GapBody d={d} f={f} n={n} season={season?.season ?? 1} />
              </Link>
              : <div className="flex items-center gap-3 rounded-2xl p-2 opacity-70"><GapBody d={d!} f={f} n={n} season={season?.season ?? 1} /></div>}
          </li>
        ))}
        {!episodes.length && !gaps.length && season?.episodes.length ? <li className="px-2 py-4 text-sm text-muted">No episode matches “{find}”.</li> : null}
      </ul>
    </>
  ) : <Empty icon="library" title="No episodes in the library">Episodes appear here once their files are in an SFW folder and identified.</Empty>
  return <TitleStage backdrop={d ? d.backdrop || d.poster || PLACEHOLDER : undefined} back="/videos" onBack={() => useScreenView.getState().setView('series')} backLabel="Back to series" left={left} panel={panel} panelLabel="Seasons and episodes" />
}
