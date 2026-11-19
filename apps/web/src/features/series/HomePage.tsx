import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '@/api/client'
import { Button } from '@/design/Button'
import { Dialog } from '@/design/Dialog'
import { HScroll } from '@/design/HScroll'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { useLabels } from '@/features/content/labels'
import { useSearch } from '@/app/search'
import { useUi } from '@/app/ui'
import { Popover } from '@/features/photos/Popover'
import { Chips } from './TitleStage'
import { MoviesList } from './MoviesPage'
import { PLACEHOLDER, PosterCard } from './PosterCard'
import { SeriesList } from './SeriesPage'
import { countText } from './series'
import { useHomeTab, useScreenView, type HomeTab } from './view'

const GENRES = ['Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western']

interface Billboard { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; overview: string; backdrop: string; poster: string; genres: string[]; rating: number | null }
interface Resume { cid: string; mid: number; title: string; sub: string; image: string; pct: number; left: number }
interface Airing { id: number; title: string; poster: string; season: number; episode: number; name: string; date: string }
interface Recent { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string }
interface HomeData { continue: Resume[]; airing: Airing[]; recent: Recent[]; billboard: Billboard[] }
interface Found { imdb: string; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; background: string; rating: string; overview: string; genres: string[]; library: { kind: 'tv' | 'movie'; id: number } | null }
interface FoundPage { items: Found[]; more?: boolean; disabled?: boolean }
interface FoundOne extends Found { runtime: string; cast: string[]; director: string[]; trailer: string; seasons: number }

const openIn = (kind: 'tv' | 'movie') => (kind === 'tv' ? '/series/$id' : '/movies/$id')

/** A row of things to scroll sideways, under a heading; arrows appear on the sides when something is out of view. */
function Row({ title, hint, children, arrowTop = 132, onSeeAll }: { title: string; hint?: string; children: ReactNode; arrowTop?: number; onSeeAll?: () => void }) {
  return (
    <section aria-label={title} className="grid gap-3">
      <div className="flex items-baseline justify-between gap-3"><h2 className="text-xl font-extrabold tracking-tight">{title}</h2>{onSeeAll ? <button type="button" onClick={onSeeAll} className="text-[13px] font-semibold text-muted transition-colors hover:text-fg">See all →</button> : hint ? <span className="text-[13px] text-muted">{hint}</span> : null}</div>
      <HScroll label={`${title}, scroll sideways`} arrowTop={arrowTop} className="-mx-2 flex snap-x gap-4 px-2 pb-3 pt-1">{children}</HScroll>
    </section>
  )
}

/** A poster in a row: a fixed width, so a row scrolls instead of squeezing. */
function Poster(p: { to?: string; params?: Record<string, string>; image: string; title: string; sub?: string; badge?: string; rating?: string | number | null }) {
  return <PosterCard {...p} className="w-[168px] shrink-0 snap-start" />
}

const HERO_MS = 9000
const reducedMotion = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

/**
 * The featured titles of your library, one at a time: each picture pushes in slowly while it is shown and fades into the next every nine
 * seconds, with a dot for each title that fills as the time runs out. Nothing moves for a person who asked for less motion.
 */
function Hero({ items }: { items: Billboard[] }) {
  const [i, setI] = useState(0)
  const [still] = useState(reducedMotion)
  const n = items.length
  useEffect(() => {
    if (n < 2 || still) return
    const t = setTimeout(() => setI((x) => (x + 1) % n), HERO_MS)
    return () => clearTimeout(t)
  }, [n, i, still])
  const at = i % Math.max(1, n)
  const b = items[at]
  if (!b) return null
  return (
    <section aria-label="Featured" className="relative isolate -mb-28 min-h-[min(84vh,780px)] overflow-hidden bg-surface-2">
      {items.map((x, k) => <img key={x.id} src={x.backdrop || x.poster || PLACEHOLDER} alt="" aria-hidden="true" style={{ animationPlayState: k === at ? 'running' : 'paused' }} className={cn('mo-hero-img absolute inset-0 -z-10 size-full object-cover object-[70%_25%]', k === at ? 'opacity-100' : 'opacity-0')} />)}
      <div aria-hidden="true" className="absolute inset-0 -z-10 bg-[linear-gradient(0deg,var(--color-bg)_2%,color-mix(in_srgb,var(--color-bg)_65%,transparent)_20%,transparent_50%),linear-gradient(90deg,rgb(0_0_0/0.82)_0%,rgb(0_0_0/0.45)_38%,transparent_70%)]" />
      <div key={b.id} className="mo-enter relative flex min-h-[inherit] max-w-3xl flex-col justify-end gap-4 px-4 pb-40 pt-44 text-white sm:px-10">
        <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-white/80">From your library</span>
        <h2 className="text-[clamp(40px,6.2vw,92px)] font-black leading-[0.92] tracking-[-0.04em] drop-shadow-xl">{b.title}</h2>
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[15px] font-medium text-white/90">
          {b.year ? <span>{b.year}</span> : null}
          <span className="rounded border border-white/60 px-1.5 text-xs font-semibold">{b.kind === 'tv' ? 'Series' : 'Movie'}</span>
          {b.genres.length ? <span>{b.genres.slice(0, 3).join(' · ')}</span> : null}
          {b.rating ? <span>{Number(b.rating).toFixed(1)} rating</span> : null}
        </p>
        {b.overview ? <p className="line-clamp-3 max-w-xl text-base leading-relaxed text-white/90">{b.overview}</p> : null}
        <div className="mt-1 flex items-center gap-3">
          <Link to={openIn(b.kind)} params={{ id: String(b.id) }} className="inline-flex h-12 items-center gap-2 rounded-lg bg-white px-6 text-[15px] font-bold text-black transition-[transform,background-color] hover:bg-white/90 active:scale-[0.97]"><Icon name="play" className="size-4" />Open</Link>
        </div>
      </div>
      {n > 1 ? <span className="absolute bottom-44 right-4 flex gap-2 sm:right-10" aria-hidden="true">{items.map((x, k) => (
        <button key={x.id} type="button" tabIndex={-1} onClick={() => setI(k)} className={cn('mo-dot relative h-[3px] overflow-hidden rounded-full bg-white/35', k === at ? 'w-12' : 'w-8')}>
          {k === at ? <span key={i} className={cn('absolute inset-0 bg-white', still ? '' : 'mo-dot-fill')} /> : null}
        </button>))}</span> : null}
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

/** True on a window wide enough to keep the details beside the posters instead of in a pop-up. */
function useWide(): boolean {
  const query = '(min-width: 1024px)'
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && Boolean(window.matchMedia?.(query).matches))
  useEffect(() => {
    const m = window.matchMedia?.(query)
    if (!m) return
    const f = () => setWide(m.matches)
    f()
    m.addEventListener('change', f)
    return () => m.removeEventListener('change', f)
  }, [])
  return wide
}

/** The details of the title the pointer rests on, kept beside the posters: its picture fades in behind its story, its genres, cast and directors. */
function DetailPanel({ item }: { item: Found }) {
  const q = useQuery({ queryKey: ['discover-one', item.kind, item.imdb] as const, queryFn: () => api<FoundOne>(`/api/screen/discover/${item.kind === 'tv' ? 'series' : 'movie'}/${item.imdb}`), retry: false, staleTime: 10 * 60_000 })
  const d = q.data ?? { ...item, runtime: '', cast: [], director: [], trailer: '', seasons: 0 }
  const lib = d.library ?? item.library
  return (
    <aside aria-label="Details" className="relative isolate self-start overflow-hidden rounded-[26px] border border-line bg-surface lg:sticky lg:top-20">
      <img key={item.imdb} src={d.background || d.poster || PLACEHOLDER} alt="" aria-hidden="true" className="mo-fade-in absolute inset-0 -z-20 size-full object-cover opacity-40" />
      <div aria-hidden="true" className="absolute inset-0 -z-10 bg-[linear-gradient(0deg,var(--color-surface)_18%,color-mix(in_srgb,var(--color-surface)_55%,transparent)_100%)]" />
      <div key={item.imdb} className="mo-panel-in grid gap-4 p-6">
        <h3 className="pt-6 text-3xl font-black leading-[1.05] tracking-tight">{item.title}</h3>
        <p className="m-0 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-semibold">
          {[d.runtime, d.year, item.kind === 'tv' ? (d.seasons ? countText(d.seasons, 'season') : 'Series') : ''].filter(Boolean).map((t) => <span key={String(t)}>{t}</span>)}
          {d.rating ? <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="text-amber-300">★</span>{d.rating}</span> : null}
        </p>
        <p className="line-clamp-6 text-[15px] leading-relaxed text-fg/85">{d.overview || (q.isPending ? 'Loading…' : 'No description.')}</p>
        <Chips label="Genres" items={d.genres} />
        <Chips label="Cast" items={d.cast.slice(0, 4)} />
        <Chips label="Directors" items={d.director.slice(0, 2)} />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {lib ? <Link to={openIn(lib.kind)} params={{ id: String(lib.id) }} className="inline-flex h-11 items-center gap-2 rounded-full bg-fg px-6 text-sm font-bold text-bg transition-transform active:scale-[0.97]"><Icon name="play" className="size-4" />Open in your library</Link>
            : <span className="inline-flex h-11 items-center rounded-full border border-line-2 px-5 text-sm text-muted">Not in your library</span>}
          {d.trailer ? <a href={`https://www.youtube.com/watch?v=${d.trailer}`} target="_blank" rel="noreferrer" className="inline-flex h-11 items-center rounded-full border border-line-2 px-5 text-sm font-semibold transition-colors hover:bg-fg/10">Watch the trailer</a> : null}
        </div>
      </div>
    </aside>
  )
}

type Kind = 'movie' | 'series'
const OPTION = 'h-9 rounded-full border px-3.5 text-[13px] font-semibold transition-colors'

/** One filter for Discover, like the one on the Photos page: a pill with a count of what is chosen, opening a panel with the kind and the genre. */
function DiscoverFilters({ kind, setKind, genre, setGenre, off }: { kind: Kind; setKind: (k: Kind) => void; genre: string; setGenre: (g: string) => void; off: boolean }) {
  const count = (kind === 'series' ? 1 : 0) + (genre ? 1 : 0)
  const pick = (on: boolean) => cn(OPTION, on ? 'border-accent/60 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')
  return (
    <div className={cn(off && 'pointer-events-none opacity-50')} aria-disabled={off || undefined}>
      <Popover label="Filters" icon={<Icon name="filter" className="size-4" />} badge={count} active={count > 0} align="right">
        <div className="grid gap-4">
          <div className="grid gap-2">
            <h3 className="text-sm font-bold">Kind</h3>
            <div role="group" aria-label="Kind" className="flex gap-1.5">
              {([['movie', 'Movies'], ['series', 'Series']] as const).map(([v, label]) => <button key={v} type="button" aria-pressed={kind === v} onClick={() => setKind(v)} className={pick(kind === v)}>{label}</button>)}
            </div>
          </div>
          <div className="grid gap-2">
            <h3 className="text-sm font-bold">Genre</h3>
            <div role="group" aria-label="Genre" className="flex flex-wrap gap-1.5">
              {['', ...GENRES].map((g) => <button key={g || 'all'} type="button" aria-pressed={genre === g} onClick={() => setGenre(g)} className={pick(genre === g)}>{g || 'All genres'}</button>)}
            </div>
          </div>
          {count ? <button type="button" onClick={() => { setKind('movie'); setGenre('') }} className="justify-self-start text-[13px] font-semibold text-muted hover:text-fg">Clear filters</button> : null}
        </div>
      </Popover>
    </div>
  )
}

/**
 * Discover: what is popular in Cinemeta's open catalogues, by kind and genre, with a search; the titles you already have are marked.
 * On a wide window the pointer resting on a poster shows that title's details in a panel beside the grid; on a narrow one a click opens them.
 */
function Discover({ text, kind, setKind, genre, setGenre }: { text: string; kind: Kind; setKind: (k: Kind) => void; genre: string; setGenre: (g: string) => void }) {
  const [open, setOpen] = useState<Found | null>(null)
  const [focus, setFocus] = useState<Found | null>(null)
  const wide = useWide()
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
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
  const shown = wide ? (items.find((x) => x.imdb === focus?.imdb && x.kind === focus.kind) ?? items[0] ?? null) : null
  const rest = (it: Found) => { clearTimeout(timer.current); timer.current = setTimeout(() => setFocus(it), 120) }
  return (
    <section aria-label="Discover" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-extrabold tracking-tight">{search ? `Results for “${search}”` : genre ? `${genre} ${kind === 'movie' ? 'movies' : 'series'}` : `Popular ${kind === 'movie' ? 'movies' : 'series'}`}</h2>
        <DiscoverFilters kind={kind} setKind={(k) => { setKind(k); setFocus(null) }} genre={genre} setGenre={(g) => { setGenre(g); setFocus(null) }} off={Boolean(search)} />
      </div>
      {off ? <p role="status" className="rounded-2xl border border-dashed border-line p-6 text-sm text-muted">Discover uses Cinemeta, the Stremio project's open catalogue, and it is switched off. Turn it on in Settings, Metadata.</p>
        : q.isError ? <p role="alert" className="rounded-2xl border border-dashed border-line p-6 text-sm text-warn">The catalogue could not be reached. Check the internet connection and try again.</p>
        : q.isPending ? <p role="status" className="py-6 text-sm text-muted">Loading the catalogue…</p>
        : !items.length ? <p role="status" className="py-6 text-sm text-muted">Nothing found.</p>
        : (
          <div className={cn('grid gap-6', wide && 'grid-cols-[minmax(0,1fr)_380px]')}>
            <div className="grid content-start gap-5">
              <ul aria-label="Catalogue" className={cn('m-0 grid list-none gap-x-4 gap-y-5 p-0', wide ? 'grid-cols-[repeat(auto-fill,minmax(150px,1fr))]' : 'grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(168px,1fr))]')}>
                {items.map((it, k) => (
                  <li key={`${it.kind}:${it.imdb}`} className="mo-enter m-0" style={{ animationDelay: `${Math.min(k % 50, 14) * 25}ms` }}>
                    <PosterCard image={it.poster} title={it.title} sub={it.year ? String(it.year) : ''} rating={it.rating} active={shown?.imdb === it.imdb && shown.kind === it.kind}
                      corner={it.library ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null}
                      onHover={wide ? () => rest(it) : undefined} onClick={() => { if (wide) { clearTimeout(timer.current); setFocus(it) } else setOpen(it) }} />
                  </li>
                ))}
              </ul>
              {q.hasNextPage ? <div className="flex justify-center"><Button disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>{q.isFetchingNextPage ? 'Loading…' : 'Show more'}</Button></div> : null}
            </div>
            {shown ? <DetailPanel item={shown} /> : null}
          </div>
        )}
      {open ? <Details item={open} onClose={() => setOpen(null)} /> : null}
    </section>
  )
}

/** A row of what is popular in the open catalogue (movies or series), under For you; "See all" opens it in Discover. */
function PopularRow({ kind, onSeeAll }: { kind: Kind; onSeeAll: () => void }) {
  const [open, setOpen] = useState<Found | null>(null)
  const q = useQuery({ queryKey: ['discover-popular', kind] as const, queryFn: () => api<FoundPage>(`/api/screen/discover?type=${kind}&skip=0`), staleTime: 5 * 60_000, retry: false, select: (p) => ({ ...p, items: p.items.slice(0, 14) }) })
  if (!q.data?.items.length) return null
  return (
    <>
      <Row title={kind === 'movie' ? 'Popular movies' : 'Popular series'} onSeeAll={onSeeAll}>
        {q.data.items.map((it) => <PosterCard key={`${it.kind}:${it.imdb}`} image={it.poster} title={it.title} sub={it.year ? String(it.year) : ''} rating={it.rating} className="w-[168px] shrink-0 snap-start" corner={it.library ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null} onClick={() => setOpen(it)} />)}
      </Row>
      {open ? <Details item={open} onClose={() => setOpen(null)} /> : null}
    </>
  )
}

const TABS: ReadonlyArray<readonly [HomeTab, string]> = [['home', 'For you'], ['discover', 'Discover'], ['library', 'Library']]

/**
 * Home for movies and shows (SFW), in three parts under one heading: For you (what to carry on with, what airs, what was added, and what is
 * popular), Discover (the open catalogue by kind and genre, with details beside the posters) and Library (all your series or movies).
 * Typing in the search box shows Discover's results from any part.
 */
export function SfwHome() {
  const labels = useLabels()
  const view = useScreenView((s) => s.view)
  const stored = useHomeTab((s) => s.tab)
  const setStored = useHomeTab((s) => s.setTab)
  const home = useQuery({ queryKey: ['screen-home'] as const, queryFn: () => api<HomeData>('/api/screen/home'), staleTime: 30_000 })
  const text = useSearch((x) => x.q)
  const setText = useSearch((x) => x.set)
  const [kind, setKind] = useState<Kind>('movie')
  const [genre, setGenre] = useState('')
  const [lib, setLib] = useState<'series' | 'movies'>(view === 'series' ? 'series' : 'movies')
  const searching = text.trim().length >= 2
  const tab: HomeTab = searching ? 'discover' : view !== 'all' && stored === 'home' ? 'library' : stored
  const go = (t: HomeTab) => { setStored(t); if (t !== 'library') useScreenView.getState().setView('all') }
  const seeAll = (k: Kind) => { setKind(k); setGenre(''); go('discover') }
  const d = home.data
  const setImmersive = useUi((x) => x.setImmersive)
  const bleed = tab === 'home' && !searching && Boolean(d?.billboard.length)
  useEffect(() => { setImmersive(bleed); return () => setImmersive(false) }, [bleed, setImmersive])
  const tabs = (glass: boolean) => (
    <div role="tablist" aria-label="Home" className={cn('flex w-fit gap-1 rounded-full p-1', glass ? 'bg-black/35 text-white backdrop-blur' : 'mb-6 bg-fg/10')}>
      {TABS.map(([id, label]) => (
        <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => { setText(''); go(id) }}
          className={cn('rounded-full px-5 py-2 text-sm font-semibold transition-[background-color,color] duration-300', tab === id ? (glass ? 'bg-white text-black' : 'bg-fg text-bg') : glass ? 'text-white/85 hover:text-white' : 'text-fg/80 hover:text-fg')}>{label}</button>
      ))}
    </div>
  )
  return (
    <section className={cn('flex w-full flex-1 flex-col', !bleed && 'px-4 pb-10 pt-6 sm:px-10')}>
      {bleed ? null : (
        <>
          <PageHead title={labels.videos} blurb={labels.videosBlurb} />
          {tabs(false)}
        </>
      )}
      <div key={tab} className={cn('mo-enter', bleed ? 'flex flex-col' : 'grid gap-8')}>
        {tab === 'home' ? (
          <>
            {bleed && d ? (
              <div className="relative">
                <Hero items={d.billboard} />
                <div className="absolute inset-x-0 top-20 z-10 flex flex-wrap items-center justify-between gap-3 px-4 sm:px-10">{tabs(true)}</div>
              </div>
            ) : null}
            <div className={cn('grid gap-8', bleed && 'relative z-10 px-4 pb-10 sm:px-10')}>
            {d?.continue.length ? (
              <Row title="Continue watching" hint="Where you stopped" arrowTop={80}>
                {d.continue.map((r) => (
                  <Link key={`${r.cid}:${r.mid}`} to="/watch/$cid/$mid" params={{ cid: r.cid, mid: String(r.mid) }} aria-label={`Continue ${r.title}`} className="mo-poster group relative block aspect-video w-[280px] shrink-0 snap-start overflow-hidden rounded-[18px] border border-white/10 bg-surface-2">
                    <img src={r.image || PLACEHOLDER} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" />
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
            <PopularRow kind="movie" onSeeAll={() => seeAll('movie')} />
            <PopularRow kind="series" onSeeAll={() => seeAll('series')} />
            </div>
          </>
        ) : tab === 'discover' ? <Discover text={text} kind={kind} setKind={setKind} genre={genre} setGenre={setGenre} />
        : (
          <section aria-label="Your library" className="grid gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-xl font-extrabold tracking-tight">Your library</h2>
              <div role="group" aria-label="Which part" className="flex gap-1.5">
                {([['series', 'Series'], ['movies', 'Movies']] as const).map(([v, label]) => <button key={v} type="button" aria-pressed={lib === v} onClick={() => setLib(v)} className={cn('h-9 rounded-full border px-4 text-sm font-semibold transition-colors', lib === v ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>{label}</button>)}
              </div>
            </div>
            {lib === 'series' ? <SeriesList /> : <MoviesList />}
          </section>
        )}
      </div>
    </section>
  )
}
