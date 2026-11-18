import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { api } from '@/api/client'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { ScreenTabs } from './ScreenTabs'
import { MOVIE_SORTS, movieBadge, sortMovies, versionText, type MovieCard, type MovieDetail, type MovieSort } from './series'
import { lengthText } from './series'

const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/** The movies in the library: one poster for each, however many copies there are. */
export function MoviesPage() {
  const [sort, setSort] = useState<MovieSort>('title')
  const [find, setFind] = useState('')
  const [cam, setCam] = useState(false)
  const q = useQuery({ queryKey: ['movies', cam], queryFn: () => api<{ items: MovieCard[] }>(`/api/screen/movies${cam ? '?cam=1' : ''}`) })
  const items = sortMovies(q.data?.items ?? [], sort, find)
  return (
    <div className="px-4 py-5 md:px-8">
      <ScreenTabs active="movies" />
      <PageHead title="Movies" blurb="One poster for each movie, with its copies under it">
        <div className="flex flex-wrap items-center gap-2">
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a movie" aria-label="Find a movie" className="h-10 w-44 rounded-xl border border-line-2 bg-bg px-3 text-sm" />
          <select aria-label="Sort movies" value={sort} onChange={(e) => setSort(e.target.value as MovieSort)} className="h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm">{MOVIE_SORTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
          <label className="inline-flex items-center gap-2 text-sm text-muted"><input type="checkbox" checked={cam} onChange={(e) => setCam(e.target.checked)} />Show cinema recordings</label>
        </div>
      </PageHead>
      {q.isPending ? <p role="status" className="py-8 text-center text-sm text-muted">Loading movies…</p>
        : q.isError ? <p role="alert" className="py-8 text-center text-sm text-warn">Could not load the movies.</p>
        : !items.length ? <Empty icon="play" title={find ? 'No movie matches' : 'No movies yet'}>{find ? 'Try other words.' : 'Add a folder of movies in Settings → Local files (marked SFW), then identify it in Settings → Metadata.'}</Empty>
        : (
          <ul aria-label="Movies" className="m-0 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
            {items.map((m, i) => (
              <li key={m.id} className="card-in m-0" style={{ '--i': i } as React.CSSProperties}>
                <Link to="/movies/$id" params={{ id: String(m.id) }} aria-label={`Open ${m.title}`} className="ax-lift group relative block aspect-[2/3] overflow-hidden rounded-[23px] border border-white/10 bg-surface-2">
                  <img src={m.poster || PLACEHOLDER} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
                  <span className={cn('absolute right-2 top-2 rounded-full px-2 py-0.5 text-[11px] font-bold', m.cam_only ? 'bg-warn/90 text-black' : 'bg-black/65 text-white')}>{movieBadge(m)}</span>
                  <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.9)_60%)] p-3 pt-10">
                    <span className="truncate text-sm font-bold text-white" title={m.title}>{m.title}</span>
                    <span className="text-xs text-white/80">{m.year ?? ''}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
    </div>
  )
}

/** One movie: the story, the cast, and every copy of it, best first. */
export function MovieDetailPage({ id }: { id: number }) {
  const [cam, setCam] = useState(false)
  const q = useQuery({ queryKey: ['movies', id, cam], queryFn: () => api<MovieDetail>(`/api/screen/movies/${id}${cam ? '?cam=1' : ''}`) })
  const d = q.data
  return (
    <div className="px-4 py-5 md:px-8">
      <Link to="/movies" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-muted hover:text-fg"><Icon name="arrow-left" className="size-4" />Movies</Link>
      {q.isPending ? <p role="status" className="py-8 text-center text-sm text-muted">Loading…</p>
        : q.isError || !d ? <p role="alert" className="py-8 text-center text-sm text-warn">This movie is not in the library.</p>
        : (
          <>
            <header className="mb-6 flex flex-col gap-5 sm:flex-row">
              <img src={d.poster || PLACEHOLDER} alt="" className="aspect-[2/3] w-40 shrink-0 rounded-2xl border border-line object-cover" />
              <div className="grid content-start gap-2">
                <h1 className="text-[clamp(25px,2vw,32px)] font-extrabold tracking-[-0.03em]">{d.title}{d.year ? <span className="ml-2 text-lg font-semibold text-muted">{d.year}</span> : null}</h1>
                {d.tagline ? <p className="text-sm italic text-muted">{d.tagline}</p> : null}
                <p className="max-w-3xl text-[15px] text-muted">{d.overview}</p>
                <ul aria-label="Genres" className="m-0 flex list-none flex-wrap gap-1.5 p-0">{d.genres.map((g) => <li key={g} className="rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-semibold text-accent">{g}</li>)}</ul>
                {d.companies.length ? <p className="text-xs text-faint">{d.companies.join(' · ')}</p> : null}
                {d.cast.length ? <p className="text-sm"><span className="font-semibold">Cast: </span><span className="text-muted">{d.cast.slice(0, 8).map((c) => c.name).join(', ')}</span></p> : null}
              </div>
            </header>
            <section aria-label="Copies" className="grid gap-2">
              <h2 className="flex items-center justify-between text-lg font-bold">{d.versions.length === 1 ? 'Watch' : `Choose a copy (${d.versions.length})`}
                <label className="inline-flex items-center gap-2 text-xs font-medium text-muted"><input type="checkbox" checked={cam} onChange={(e) => setCam(e.target.checked)} />Show cinema recordings</label></h2>
              <ul className="m-0 grid list-none gap-2 p-0">
                {d.versions.map((v, i) => (
                  <li key={`${v.cid}:${v.mid}`} className="m-0">
                    <Link to="/watch/$cid/$mid" params={{ cid: v.cid, mid: String(v.mid) }} className="ax-lift flex items-center gap-3 rounded-2xl border border-line bg-surface p-3 hover:bg-fg/5">
                      <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent/15 text-accent"><Icon name="play" className="size-5" /></span>
                      <span className="min-w-0 flex-1"><span className="block font-semibold">{versionText(v) || v.filename}{i === 0 && d.versions.length > 1 ? <span className="ml-2 rounded-full bg-good/15 px-2 py-0.5 text-[11px] font-bold text-good">Best</span> : null}</span>
                        <span className="block truncate text-xs text-faint" title={v.filename}>{v.filename}</span></span>
                      <span className="shrink-0 text-xs text-faint">{lengthText({ duration: v.duration, runtime: d.runtime })}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
    </div>
  )
}
