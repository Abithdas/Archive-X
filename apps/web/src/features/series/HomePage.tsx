import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '@/api/client'
import { Button } from '@/design/Button'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { useLabels } from '@/features/content/labels'
import { MoviesList } from './MoviesPage'
import { SeriesList } from './SeriesPage'
import { countText } from './series'
import { useScreenView } from './view'

const PLACEHOLDER = '/static/icons/ax-placeholder.png'
const GENRES = ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'Horror', 'Mystery', 'Romance', 'Sci-Fi', 'Thriller', 'War', 'Western']

interface Billboard { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; overview: string; backdrop: string; poster: string; genres: string[]; rating: number | null }
interface Resume { cid: string; mid: number; title: string; sub: string; image: string; pct: number; left: number }
interface Airing { id: number; title: string; poster: string; season: number; episode: number; name: string; date: string }
interface Recent { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string }
interface HomeData { continue: Resume[]; airing: Airing[]; recent: Recent[]; billboard: Billboard[] }
interface Found { imdb: string; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; background: string; rating: string; overview: string; genres: string[]; library: { kind: 'tv' | 'movie'; id: number } | null }
interface FoundPage { items: Found[]; more?: boolean; disabled?: boolean }
interface FoundOne extends Found { runtime: string; cast: string[]; director: string[]; trailer: string; seasons: number }

const openIn = (kind: 'tv' | 'movie') => (kind === 'tv' ? '/series/$id' : '/movies/$id')

/** A row of things to scroll sideways, under a heading. */
function Row({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="grid gap-3">
      <div className="flex items-baseline justify-between gap-3"><h2 className="text-xl font-extrabold tracking-tight">{title}</h2>{hint ? <span className="text-[13px] text-muted">{hint}</span> : null}</div>
      <div className="-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-2 [scrollbar-width:thin]">{children}</div>
    </section>
  )
}

function Poster({ to, params, image, title, sub, badge, onClick }: { to?: string; params?: Record<string, string>; image: string; title: string; sub?: string; badge?: string; onClick?: () => void }) {
  const inner = (
    <>
      <img src={image || PLACEHOLDER} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.04]" />
      {badge ? <span className="absolute left-2 top-2 rounded-full bg-black/70 px-2 py-0.5 text-[11px] font-bold text-white">{badge}</span> : null}
      <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.9)_60%)] p-3 pt-10">
        <span className="truncate text-sm font-bold text-white" title={title}>{title}</span>
        {sub ? <span className="truncate text-xs text-white/80">{sub}</span> : null}
      </span>
    </>
  )
  const cls = 'ax-lift group relative block aspect-[2/3] w-[168px] shrink-0 snap-start overflow-hidden rounded-[22px] border border-white/10 bg-surface-2 text-left'
  return to ? <Link to={to} params={params} aria-label={`Open ${title}`} className={cls}>{inner}</Link> : <button type="button" onClick={onClick} aria-label={`Details of ${title}`} className={cls}>{inner}</button>
}

/** The featured titles of your library, one at a time: the picture across the page, what it is about, and a button to open it. */
function Hero({ items }: { items: Billboard[] }) {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (items.length < 2 || (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)) return
    const t = setInterval(() => setI((n) => (n + 1) % items.length), 9000)
    return () => clearInterval(t)
  }, [items.length])
  const b = items[i % Math.max(1, items.length)]
  if (!b) return null
  return (
    <section aria-label="Featured" className="relative isolate min-h-[300px] overflow-hidden rounded-[22px] border border-line bg-surface-2 sm:min-h-[380px]">
      <img key={b.backdrop} src={b.backdrop} alt="" className="absolute inset-0 -z-10 size-full object-cover" />
      <div className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,rgb(13_15_17/0.88)_0%,rgb(13_15_17/0.45)_55%,transparent_100%),linear-gradient(0deg,rgb(13_15_17/0.8)_0%,transparent_60%)]" />
      <div className="flex min-h-[inherit] max-w-2xl flex-col justify-end gap-3 p-6 sm:p-10">
        <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-accent">From your library</span>
        <h2 className="text-3xl font-extrabold leading-tight text-white sm:text-5xl">{b.title}</h2>
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-white/85">{[b.year, b.kind === 'tv' ? 'Series' : 'Movie', b.genres.join(', '), b.rating ? `${Number(b.rating).toFixed(1)} rating` : ''].filter(Boolean).map((t) => <span key={String(t)}>{t}</span>)}</p>
        {b.overview ? <p className="line-clamp-3 text-sm leading-relaxed text-white/85">{b.overview}</p> : null}
        <div className="mt-1 flex items-center gap-3">
          <Link to={openIn(b.kind)} params={{ id: String(b.id) }} className="inline-flex h-11 items-center rounded-xl bg-accent px-6 text-sm font-bold text-accent-fg">Open</Link>
          {items.length > 1 ? <span className="flex gap-1.5" aria-hidden="true">{items.map((x, n) => <button key={x.id} type="button" tabIndex={-1} onClick={() => setI(n)} className={cn('h-1 w-6 rounded-full transition-colors', n === i % items.length ? 'bg-accent' : 'bg-white/30')} />)}</span> : null}
        </div>
      </div>
    </section>
  )
}

function whenLabel(date: string): string {
  const days = Math.round((new Date(`${date}T12:00:00`).getTime() - new Date().setHours(12, 0, 0, 0)) / 86_400_000)
  return days <= 0 ? 'Today' : days === 1 ? 'Tomorrow' : `In ${days} days`
}

/** One title from the catalogue: its story and cast, whether you have it, and a way to open it or watch its trailer. */
function Details({ item, onClose }: { item: Found; onClose: () => void }) {
  const q = useQuery({ queryKey: ['discover-one', item.kind, item.imdb] as const, queryFn: () => api<FoundOne>(`/api/screen/discover/${item.kind === 'tv' ? 'series' : 'movie'}/${item.imdb}`), retry: false })
  const d = q.data ?? { ...item, runtime: '', cast: [], director: [], trailer: '', seasons: 0 }
  const lib = d.library ?? item.library
  return (
    <Dialog open onClose={onClose} title={item.title} wide>
      <div className="grid gap-5 sm:grid-cols-[200px_minmax(0,1fr)]">
        <img src={d.poster || PLACEHOLDER} alt="" className="aspect-[2/3] w-[200px] rounded-2xl border border-line object-cover" />
        <div className="grid content-start gap-3">
          <p className="flex flex-wrap gap-x-3 text-sm text-muted">{[d.year, item.kind === 'tv' ? (d.seasons ? countText(d.seasons, 'season') : 'Series') : 'Movie', d.runtime, d.rating ? `${d.rating} on IMDb` : ''].filter(Boolean).map((t) => <span key={String(t)}>{t}</span>)}</p>
          {d.genres.length ? <ul className="flex flex-wrap gap-1.5">{d.genres.map((g) => <li key={g} className="rounded-full bg-surface-3 px-2.5 py-0.5 text-xs font-semibold">{g}</li>)}</ul> : null}
          <p className="text-[15px] leading-relaxed">{d.overview || (q.isPending ? 'Loading…' : 'No description.')}</p>
          {d.director.length ? <p className="text-sm"><span className="text-muted">Directed by </span>{d.director.join(', ')}</p> : null}
          {d.cast.length ? <p className="text-sm"><span className="text-muted">With </span>{d.cast.join(', ')}</p> : null}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {lib ? <Link to={openIn(lib.kind)} params={{ id: String(lib.id) }} onClick={onClose} className="inline-flex h-10 items-center rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg">Open in your library</Link>
              : <span className="inline-flex h-10 items-center rounded-xl border border-line-2 px-4 text-sm text-muted">Not in your library</span>}
            {d.trailer ? <a href={`https://www.youtube.com/watch?v=${d.trailer}`} target="_blank" rel="noreferrer" className="inline-flex h-10 items-center rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5">Watch the trailer</a> : null}
          </div>
        </div>
      </div>
    </Dialog>
  )
}

/** Discover: what is popular in Cinemeta's open catalogues, by genre, with a search; the titles you already have are marked. */
function Discover({ text }: { text: string }) {
  const [kind, setKind] = useState<'movie' | 'series'>('movie')
  const [genre, setGenre] = useState('')
  const [open, setOpen] = useState<Found | null>(null)
  const search = text.trim().length >= 2 ? text.trim() : ''
  const q = useInfiniteQuery({
    queryKey: ['discover', kind, genre, search] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<FoundPage>(`/api/screen/discover?type=${kind}&skip=${pageParam}${genre && !search ? `&genre=${encodeURIComponent(genre)}` : ''}${search ? `&search=${encodeURIComponent(search)}` : ''}`),
    getNextPageParam: (last, all) => (last.more ? all.length * 50 : undefined),
    staleTime: 5 * 60_000,
    retry: false,
  })
  const items = q.data?.pages.flatMap((p) => p.items) ?? []
  const off = q.data?.pages[0]?.disabled
  return (
    <section aria-label="Discover" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-extrabold tracking-tight">{search ? `Results for “${search}”` : 'Discover'}</h2>
        <div role="tablist" aria-label="Kind" className="inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]">
          {([['movie', 'Movies'], ['series', 'Series']] as const).map(([v, label]) => (
            <button key={v} type="button" role="tab" aria-selected={kind === v} onClick={() => setKind(v)} className={cn('h-8 rounded-[9px] px-4 text-sm font-semibold transition-colors', kind === v ? 'bg-surface-3 text-fg' : 'text-muted hover:text-fg')}>{label}</button>
          ))}
        </div>
      </div>
      {!search ? (
        <div role="group" aria-label="Genre" className="flex flex-wrap gap-1.5">
          {['', ...GENRES].map((g) => <button key={g || 'all'} type="button" aria-pressed={genre === g} onClick={() => setGenre(g)} className={cn('h-8 rounded-full border px-3 text-[13px] font-semibold transition-colors', genre === g ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>{g || 'Popular'}</button>)}
        </div>
      ) : null}
      {off ? <p role="status" className="rounded-2xl border border-dashed border-line p-6 text-sm text-muted">Discover uses Cinemeta, the Stremio project's open catalogue, and it is switched off. Turn it on in Settings, Metadata.</p>
        : q.isError ? <p role="alert" className="rounded-2xl border border-dashed border-line p-6 text-sm text-warn">The catalogue could not be reached. Check the internet connection and try again.</p>
        : q.isPending ? <p role="status" className="py-6 text-sm text-muted">Loading the catalogue…</p>
        : !items.length ? <p role="status" className="py-6 text-sm text-muted">Nothing found.</p>
        : (
          <>
            <ul aria-label="Catalogue" className="m-0 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-[repeat(auto-fill,minmax(168px,1fr))]">
              {items.map((it) => (
                <li key={`${it.kind}:${it.imdb}`} className="m-0">
                  <button type="button" onClick={() => setOpen(it)} aria-label={`Details of ${it.title}`} className="ax-lift group relative block aspect-[2/3] w-full overflow-hidden rounded-[22px] border border-white/10 bg-surface-2 text-left">
                    <img src={it.poster || PLACEHOLDER} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.04]" />
                    {it.library ? <span className="absolute left-2 top-2 rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null}
                    {it.rating ? <span className="absolute right-2 top-2 rounded-full bg-black/70 px-2 py-0.5 text-[11px] font-bold text-white">{it.rating}</span> : null}
                    <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.9)_60%)] p-3 pt-10"><span className="truncate text-sm font-bold text-white" title={it.title}>{it.title}</span><span className="text-xs text-white/80">{it.year ?? ''}</span></span>
                  </button>
                </li>
              ))}
            </ul>
            {q.hasNextPage ? <div className="flex justify-center"><Button disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>{q.isFetchingNextPage ? 'Loading…' : 'Show more'}</Button></div> : null}
          </>
        )}
      {open ? <Details item={open} onClose={() => setOpen(null)} /> : null}
    </section>
  )
}

/**
 * Home for movies and shows (SFW): what to carry on with, what airs this week, what you added, your series and movies, and, below, Discover.
 * One page for what Home and Discover are in other apps; everything from your library comes first.
 */
export function SfwHome() {
  const labels = useLabels()
  const view = useScreenView((s) => s.view)
  const home = useQuery({ queryKey: ['screen-home'] as const, queryFn: () => api<HomeData>('/api/screen/home'), staleTime: 30_000 })
  const [text, setText] = useState('')
  const [all, setAll] = useState<'series' | 'movies' | null>(view === 'series' ? 'series' : view === 'movies' ? 'movies' : null)
  const field = useRef<HTMLInputElement>(null)
  const searching = text.trim().length >= 2
  const d = home.data
  return (
    <section className="flex w-full flex-1 flex-col px-4 pb-10 pt-6 sm:px-10">
      <PageHead title={labels.videos} blurb={labels.videosBlurb}>
        <label className="relative block w-full sm:w-80">
          <span className="sr-only">Search movies and shows</span>
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
          <input ref={field} type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search movies and shows" aria-label="Search movies and shows" className="h-11 w-full rounded-xl border border-line-2 bg-bg pl-9 pr-3 text-sm focus-visible:border-accent focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent" />
        </label>
      </PageHead>
      <div className="grid gap-8">
        {searching ? null : (
          <>
            {d?.billboard.length ? <Hero items={d.billboard} /> : null}
            {d?.continue.length ? (
              <Row title="Continue watching" hint="Where you stopped">
                {d.continue.map((r) => (
                  <Link key={`${r.cid}:${r.mid}`} to="/watch/$cid/$mid" params={{ cid: r.cid, mid: String(r.mid) }} aria-label={`Continue ${r.title}`} className="ax-lift group relative block aspect-video w-[280px] shrink-0 snap-start overflow-hidden rounded-[18px] border border-white/10 bg-surface-2">
                    <img src={r.image || PLACEHOLDER} alt="" loading="lazy" className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.04]" />
                    <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.92)_70%)] p-3 pt-8">
                      <span className="truncate text-sm font-bold text-white">{r.title}</span>
                      <span className="truncate text-xs text-white/80">{[r.sub, `${r.left} min left`].filter(Boolean).join(' · ')}</span>
                    </span>
                    <span className="absolute inset-x-0 bottom-0 h-1 bg-white/25"><span className="block h-full bg-accent" style={{ width: `${r.pct}%` }} /></span>
                  </Link>
                ))}
              </Row>
            ) : null}
            {d?.airing.length ? (
              <Row title="Airing this week" hint="Shows you follow">
                {d.airing.map((a) => <Poster key={`${a.id}:${a.season}:${a.episode}`} to="/series/$id" params={{ id: String(a.id) }} image={a.poster} title={a.title} sub={`S${a.season} · E${a.episode}`} badge={whenLabel(a.date)} />)}
              </Row>
            ) : null}
            {d?.recent.length ? (
              <Row title="Recently added" hint="Newest in your library">
                {d.recent.map((r) => <Poster key={`${r.kind}:${r.id}`} to={openIn(r.kind)} params={{ id: String(r.id) }} image={r.poster} title={r.title} sub={r.year ? String(r.year) : r.kind === 'tv' ? 'Series' : 'Movie'} />)}
              </Row>
            ) : null}
            <section aria-label="Your library" className="grid gap-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-xl font-extrabold tracking-tight">Your library</h2>
                <div role="group" aria-label="Which part" className="flex gap-1.5">
                  {([['series', 'Series'], ['movies', 'Movies']] as const).map(([v, label]) => <button key={v} type="button" aria-pressed={all === v} onClick={() => setAll(all === v ? null : v)} className={cn('h-8 rounded-full border px-3 text-[13px] font-semibold transition-colors', all === v ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>{all === v ? `Hide ${label.toLowerCase()}` : `All ${label.toLowerCase()}`}</button>)}
                </div>
              </div>
              {all === 'series' ? <SeriesList /> : all === 'movies' ? <MoviesList /> : <p className="text-sm text-muted">Choose Series or Movies to see everything you have, one card for each.</p>}
            </section>
          </>
        )}
        <Discover text={text} />
      </div>
    </section>
  )
}
