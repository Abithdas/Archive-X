import { useSearch } from '@/app/search'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { playClick } from '@/features/watch/fullscreen'
import { useState } from 'react'
import { api } from '@/api/client'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { Reveal } from '@/design/Reveal'
import { PLACEHOLDER, PosterCard } from './PosterCard'
import { PosterSkeleton } from './PosterSkeleton'
import { Chips, TitleStage } from './TitleStage'
import { useScreenView } from './view'
import { COPY_SORTS, MOVIE_SORTS, copyBadge, movieBadge, sortCopies, sortMovies, versionText, type CopySort, type MovieCard, type MovieDetail, type MovieSort } from './series'
import { lengthText } from './series'
import { ListFilters } from './FilterPanel'
import { NO_FILTER, filterCount, matchesFilter, type ListFilter } from './listFilters'
import { ReviewsButton, ScoreBadges, useReviews } from './Reviews'


/** The movies in the library, shown inside Movies & Shows: one poster for each, however many copies there are. */
export function MoviesList() {
  const [sort, setSort] = useState<MovieSort>('title')
  const find = useSearch((x) => x.q)   // the top bar's search is the one search box (the library list is narrowed by it)
  const [filter, setFilter] = useState<ListFilter>(NO_FILTER)
  const q = useQuery({ queryKey: ['movies'], queryFn: () => api<{ items: MovieCard[] }>('/api/screen/movies') })
  const all = q.data?.items ?? []
  const items = sortMovies(all, sort, find).filter((m) => matchesFilter(filter, m))
  const narrowed = Boolean(find.trim()) || filterCount(filter) > 0
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted">One poster for each movie, with its copies under it{q.data ? <span className="ml-2 text-faint">· {narrowed ? `${items.length.toLocaleString()} of ${all.length.toLocaleString()}` : all.length.toLocaleString()} movies</span> : null}</p>
        <div className="flex flex-wrap items-center gap-2">
          <ListFilters items={all} value={filter} onChange={setFilter} sort={{ value: sort, options: MOVIE_SORTS, onChange: (v) => setSort(v as MovieSort) }} />
        </div>
      </div>
      {q.isError ? <p role="alert" className="py-8 text-center text-sm text-warn">Could not load the movies.</p>
        : <Reveal ready={!q.isPending} skeleton={<PosterSkeleton label="Loading movies" />}>{!q.data ? null : !items.length ? <Empty icon="play" title={narrowed ? 'No movie matches' : 'No movies yet'}>{narrowed ? 'Try other words, or clear the filters.' : 'Add a folder of movies in Settings → Local files (marked SFW), then identify it in Settings → Metadata.'}</Empty>
        : (
          <ul aria-label="Movies" className="m-0 grid list-none grid-cols-2 gap-x-4 gap-y-5 p-0 sm:grid-cols-[repeat(auto-fill,minmax(170px,1fr))]">
            {items.map((m, i) => (
              <li key={m.id} className="card-in m-0" style={{ '--i': i } as React.CSSProperties}>
                <PosterCard to="/movies/$id" params={{ id: String(m.id) }} image={m.poster} title={m.title} sub={m.year ? String(m.year) : ''} rating={m.rating ? m.rating.toFixed(1) : null}
                  corner={<span className={cn('rounded-full px-2 py-0.5 text-[11px] font-bold', m.cam_only ? 'bg-warn/90 text-black' : 'bg-black/65 text-white')}>{movieBadge(m)}</span>} />
              </li>
            ))}
          </ul>
        )}</Reveal>}
    </div>
  )
}

/** One movie, laid out like a streaming app: the artwork across the page, the story on the left, and every copy of it, best first, in a panel on the right. */
export function MovieDetailPage({ id }: { id: number }) {
  const q = useQuery({ queryKey: ['movies', id], queryFn: () => api<MovieDetail>(`/api/screen/movies/${id}`) })
  const d = q.data
  const reviews = useReviews('movie', d?.imdb ?? '')
  const best = d?.versions[0]
  const [copySort, setCopySort] = useState<CopySort>('best')
  const [copyFilter, setCopyFilter] = useState<ListFilter>(NO_FILTER)
  const shownCopies = d ? sortCopies(d.versions.filter((v) => matchesFilter(copyFilter, { res: v.height ? [v.height] : [], sources: v.source ? [v.source] : [], hdr: v.hdr ?? [], langs: v.languages ?? [] })), copySort) : []
  const copyFacets = (d?.versions ?? []).map((v) => ({ res: v.height ? [v.height] : [], sources: v.source ? [v.source] : [], hdr: v.hdr ?? [], langs: v.languages ?? [] }))
  const left = q.isPending ? <p role="status" className="py-8 text-sm text-muted">Loading…</p>
    : q.isError || !d ? <p role="alert" className="py-8 text-sm text-warn">This movie is not in the library.</p>
    : (
      <div className="mo-enter mt-auto grid max-w-2xl gap-4 pt-16">
        <h1 className="text-[clamp(34px,5vw,64px)] font-black leading-[0.95] tracking-[-0.04em] drop-shadow-lg">{d.title}</h1>
        {d.tagline ? <p className="text-sm italic text-fg/70">{d.tagline}</p> : null}
        <p aria-label="Facts" className="m-0 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-semibold">
          {d.year ? <span>{d.year}</span> : null}
          {d.runtime ? <span>{d.runtime} min</span> : null}
          {d.rating ? <span className="inline-flex items-center gap-1.5">{d.rating.toFixed(1)}<span className="rounded bg-amber-400 px-1 text-[11px] font-black text-black">RATING</span></span> : null}
          <ScoreBadges data={reviews.data} />
        </p>
        <p className="max-w-xl text-[15px] leading-relaxed text-fg/80">{d.overview}</p>
        <Chips label="Genres" items={d.genres} />
        <Chips label="Cast" cast items={d.cast.slice(0, 6).map((c) => c.name)} title={(n) => d.cast.find((c) => c.name === n)?.character} />
        {d.companies.length ? <p className="text-xs text-faint">{d.companies.join(' · ')}</p> : null}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {best ? <Link to="/watch/$cid/$mid" onClick={playClick} params={{ cid: best.cid, mid: String(best.mid) }} className="inline-flex h-12 w-fit items-center gap-2 rounded-full bg-accent px-6 text-[15px] font-bold text-accent-fg transition-[filter,transform] hover:brightness-110 active:scale-[0.98]"><Icon name="play" className="size-4" />Play</Link> : null}
          <ReviewsButton data={reviews.data} title={d.title} className="inline-flex h-12 items-center rounded-full border border-line-2 px-5 text-sm font-semibold transition-colors hover:bg-fg/10" />
        </div>
      </div>
    )
  const panel = !d ? null : (
    <section aria-label="Copies" className="grid gap-2">
      <h2 className="flex items-center justify-between px-1 text-lg font-bold">{d.versions.length === 1 ? 'Watch' : `Choose a copy (${d.versions.length})`}</h2>
      {d.versions.length > 1 ? (
        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
          <ListFilters items={copyFacets} value={copyFilter} onChange={setCopyFilter} sort={{ value: copySort, options: COPY_SORTS, onChange: (v) => setCopySort(v as CopySort) }} />
          <span className="text-xs font-medium text-muted">{filterCount(copyFilter) ? `${shownCopies.length} of ${d.versions.length}` : d.versions.length} copies</span>
        </div>
      ) : null}
      {!shownCopies.length ? <p role="status" className="px-1 py-4 text-sm text-muted">No copy matches these filters. <button type="button" onClick={() => setCopyFilter(NO_FILTER)} className="font-semibold text-accent underline">Clear filters</button></p> : null}
      <ul className="m-0 grid list-none gap-1 p-0">
        {shownCopies.map((v, i) => {
          const badge = copyBadge(v)
          const langs = (v.languages ?? []).join(', ')
          return (
            <li key={`${v.cid}:${v.mid}`} className="mo-enter m-0" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
              <Link to="/watch/$cid/$mid" onClick={playClick} params={{ cid: v.cid, mid: String(v.mid) }} className="group flex items-center gap-3 rounded-2xl p-2.5 transition-colors hover:bg-fg/10 focus-visible:bg-fg/10">
                <span aria-hidden="true" className="grid h-12 w-[4.5rem] shrink-0 place-content-center rounded-lg bg-fg/10 text-center transition-colors group-hover:bg-fg group-hover:text-bg"><b className="text-[17px] font-extrabold leading-none">{badge.top}</b>{badge.bottom ? <small className="mt-0.5 text-[10px] font-semibold leading-none opacity-80">{badge.bottom}</small> : null}</span>
                <span className="min-w-0 flex-1"><span className="block font-semibold">{versionText(v) || v.filename}{i === 0 && d.versions.length > 1 && copySort === 'best' && !filterCount(copyFilter) ? <span className="ml-2 rounded-full bg-good/15 px-2 py-0.5 text-[11px] font-bold text-good">Best</span> : null}</span>
                  {langs ? <span className="block truncate text-xs text-muted">{langs}</span> : null}
                  <span className="block truncate text-xs text-faint" title={v.filename}>{v.filename}</span></span>
                <span className="shrink-0 text-xs text-muted">{lengthText({ duration: v.duration, runtime: d.runtime })}</span>
              </Link>
            </li>
          )
        })}
      </ul>
    </section>
  )
  return <TitleStage backdrop={d ? d.backdrop || d.poster || PLACEHOLDER : undefined} back="/videos" onBack={() => useScreenView.getState().setView('movies')} backLabel="Back to movies" left={left} panel={panel} panelLabel="Copies of this movie" />
}
