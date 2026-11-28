import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { cancelFullscreenPlay, playClick, playInFullscreen } from '@/features/watch/fullscreen'
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '@/api/client'
import { Button } from '@/design/Button'
import { Dialog } from '@/design/Dialog'
import { HScroll } from '@/design/HScroll'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { useLabels } from '@/features/content/labels'
import { useSearch } from '@/app/search'
import { useUi } from '@/app/ui'
import { Popover } from '@/features/photos/Popover'
import { useAddons, type Addon } from '@/features/settings/AddonsPanel'
import { Chips } from './TitleStage'
import { MoviesList } from './MoviesPage'
import { PLACEHOLDER, PosterCard } from './PosterCard'
import { useRowPreview } from './RowPreview'
import { DownloadsDialog, downloadsOf } from '@/features/collection/DownloadsDialog'
import { useCollection } from '@/features/collection/useCollection'
import { SeriesList } from './SeriesPage'
import { countText } from './series'
import { useHomeTab, useScreenView, type HomeTab } from './view'

const GENRES = ['Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western']

interface Billboard { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; overview: string; backdrop: string; poster: string; genres: string[]; rating: number | null }
/** A title watched through an add-on has no library file: its card goes back to the title's own page, where the streams are listed and the last position is picked up. */
interface Resume { cid: string; mid: number; title: string; sub: string; image: string; pct: number; left: number; /** The torrent that was playing is remembered: the card starts it again in full screen. */ resume?: boolean; addon?: { kind: 'movie' | 'series'; ident: string; season?: number | null; episode?: number | null } }
interface Airing { id: number; title: string; poster: string; season: number; episode: number; name: string; date: string }
interface Recent { id: number; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string }
interface HomeData { continue: Resume[]; airing: Airing[]; recent: Recent[]; billboard: Billboard[] }
interface Found { imdb: string; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; background: string; rating: string; overview: string; genres: string[]; library: { kind: 'tv' | 'movie'; id: number } | null }
interface FoundPage { items: Found[]; more?: boolean; next?: number; disabled?: boolean; needs_key?: boolean }
interface FoundOne extends Found { runtime: string; cast: string[]; director: string[]; trailer: string; seasons: number }
const hasPage = (item: Found) => /^(tt\d+|tmdb-\d+|kitsu:\d+)$/.test(item.imdb)
/** A poster opens the title's own page; a title with no page of its own (some anime and add-on titles) falls back to its details. */
const cardGo = (item: Found) => (hasPage(item) ? { to: '/discover/$kind/$id', params: pageParams(item) } : {})
const pageParams = (item: Found) => ({ kind: item.kind === 'tv' ? 'series' : 'movie', id: item.imdb })

const openIn = (kind: 'tv' | 'movie') => (kind === 'tv' ? '/series/$id' : '/movies/$id')

/** A row of things to scroll sideways, under a heading; arrows appear on the sides when something is out of view. */
function Row({ title, hint, children, arrowTop = 132, onSeeAll }: { title: string; hint?: string; children: ReactNode; arrowTop?: number; onSeeAll?: () => void }) {
  return (
    <section aria-label={title} className="grid gap-3">
      <div className="flex items-baseline justify-between gap-3"><h2 className="text-xl font-extrabold tracking-tight">{title}</h2>{onSeeAll ? <button type="button" onClick={onSeeAll} className="text-[13px] font-semibold text-muted transition-colors hover:text-fg">See all →</button> : hint ? <span className="text-[13px] text-muted">{hint}</span> : null}</div>
      <HScroll label={`${title}, scroll sideways`} arrowTop={arrowTop} className="-mx-4 -mb-7 -mt-2 flex snap-x gap-4 px-4 pb-10 pt-3">{children}</HScroll>
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
  const [seen, setSeen] = useState(true)   // scrolled out of sight: stop the slideshow and the push-in
  const box = useRef<HTMLElement>(null)
  const last = useRef(0)   // the picture that is fading out; the next one is kept ready (hidden) so the fade has something to fade to
  const n = items.length
  useEffect(() => {
    const el = box.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const o = new IntersectionObserver(([e]) => setSeen(Boolean(e?.isIntersecting)), { threshold: 0.05 })
    o.observe(el)
    return () => o.disconnect()
  }, [])
  useEffect(() => {
    if (n < 2 || still || !seen) return
    const t = setTimeout(() => setI((x) => { last.current = x; return (x + 1) % n }), HERO_MS)
    return () => clearTimeout(t)
  }, [n, i, still, seen])
  const at = i % Math.max(1, n)
  const b = items[at]
  if (!b) return null
  return (
    <section ref={box} aria-label="Featured" className="relative isolate -mb-28 min-h-[min(84vh,780px)] overflow-hidden bg-surface-2">
      {items.map((x, k) => (k === at || k === last.current % Math.max(1, n) || k === (at + 1) % Math.max(1, n)) ? <img key={x.id} src={x.backdrop || x.poster || PLACEHOLDER} alt="" aria-hidden="true" style={{ animationPlayState: k === at && seen ? 'running' : 'paused' }} className={cn('mo-hero-img absolute inset-0 -z-10 size-full object-cover object-[70%_25%]', k === at ? 'opacity-100' : 'opacity-0')} /> : null)}
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
  const q = useQuery({ queryKey: ['discover-one', item.kind, item.imdb] as const, queryFn: () => api<FoundOne>(`/api/screen/discover/${item.kind === 'tv' ? 'series' : 'movie'}/${item.imdb}`), retry: false, enabled: hasPage(item) })
  const d = q.data ?? { ...item, runtime: '', cast: [], director: [], trailer: '', seasons: 0 }
  const lib = d.library ?? item.library
  return (
    <Dialog open onClose={onClose} title={item.title} wide>
      <div className="grid gap-5 sm:grid-cols-[200px_minmax(0,1fr)]">
        <img src={d.poster || PLACEHOLDER} alt="" className="aspect-[2/3] w-[200px] rounded-2xl border border-line object-cover" />
        <div className="grid content-start gap-3">
          <p className="flex flex-wrap gap-x-3 text-sm text-muted">{[d.year, item.kind === 'tv' ? (d.seasons ? countText(d.seasons, 'season') : 'Series') : 'Movie', d.runtime, d.rating ? `${d.rating} on IMDb` : ''].filter(Boolean).map((t) => <span key={String(t)}>{t}</span>)}</p>
          {d.genres.length ? <ul className="flex flex-wrap gap-1.5">{d.genres.map((g) => <li key={g} className="rounded-full bg-surface-3 px-2.5 py-0.5 text-xs font-semibold">{g}</li>)}</ul> : null}
          <p className="text-[15px] leading-relaxed">{d.overview || (q.isLoading ? 'Loading…' : 'No description.')}</p>
          {d.director.length ? <p className="text-sm"><span className="text-muted">Directed by </span>{d.director.join(', ')}</p> : null}
          {d.cast.length ? <p className="text-sm"><span className="text-muted">With </span>{d.cast.join(', ')}</p> : null}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {lib ? <Link to={openIn(lib.kind)} params={{ id: String(lib.id) }} onClick={onClose} className="inline-flex h-10 items-center rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg">Open in your library</Link>
              : <span className="inline-flex h-10 items-center rounded-xl border border-line-2 px-4 text-sm text-muted">Not in your library</span>}
            {hasPage(item) ? <Link to="/discover/$kind/$id" params={pageParams(item)} onClick={onClose} className="inline-flex h-10 items-center rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5">Open the page</Link> : null}
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
  const q = useQuery({ queryKey: ['discover-one', item.kind, item.imdb] as const, queryFn: () => api<FoundOne>(`/api/screen/discover/${item.kind === 'tv' ? 'series' : 'movie'}/${item.imdb}`), retry: false, staleTime: 10 * 60_000, enabled: hasPage(item) })   // a title with no IMDb number (anime, some add-ons): its card already holds what the list knows
  const d = q.data ?? { ...item, runtime: '', cast: [], director: [], trailer: '', seasons: 0 }
  const lib = d.library ?? item.library
  return (
    <aside aria-label="Details" className="relative isolate min-w-0 self-start overflow-y-auto overflow-x-hidden rounded-[26px] border border-line bg-surface lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)]">
      <img key={item.imdb} src={d.background || d.poster || PLACEHOLDER} alt="" aria-hidden="true" className="mo-fade-in absolute inset-0 -z-20 size-full object-cover opacity-40" />
      <div aria-hidden="true" className="absolute inset-0 -z-10 bg-[linear-gradient(0deg,var(--color-surface)_18%,color-mix(in_srgb,var(--color-surface)_55%,transparent)_100%)]" />
      <div key={item.imdb} className="mo-panel-in grid gap-4 p-6">
        <h3 className="pt-6 text-3xl font-black leading-[1.05] tracking-tight">{item.title}</h3>
        <p className="m-0 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-semibold">
          {[d.runtime, d.year, item.kind === 'tv' ? (d.seasons ? countText(d.seasons, 'season') : 'Series') : ''].filter(Boolean).map((t) => <span key={String(t)}>{t}</span>)}
          {d.rating ? <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="text-amber-300">★</span>{d.rating}</span> : null}
        </p>
        <p className="line-clamp-6 text-[15px] leading-relaxed text-fg/85">{d.overview || (q.isLoading ? 'Loading…' : 'No description.')}</p>
        <Chips label="Genres" items={d.genres} />
        <Chips label="Cast" items={d.cast.slice(0, 4)} />
        <Chips label="Directors" items={d.director.slice(0, 2)} />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {lib ? null : <span className="inline-flex h-11 items-center rounded-full border border-line-2 px-5 text-sm text-muted">Not in your library</span>}
        </div>
      </div>
    </aside>
  )
}

type Kind = 'movie' | 'series'
type Catalog = 'popular' | 'new' | 'featured' | 'netflix' | 'hbomax' | 'disney' | 'prime' | 'apple' | 'anime-airing' | 'anime-series' | 'anime-movies' | `addon:${string}`
/** The lists Discover can show: three from Cinemeta, what is on each streaming service, and MyAnimeList's top anime (those three are anime of both kinds, so Kind does not apply). */
const CATALOGS: ReadonlyArray<readonly [Catalog, string]> = [['popular', 'Popular'], ['new', 'New'], ['featured', 'Featured'], ['netflix', 'Netflix'], ['hbomax', 'HBO Max'], ['disney', 'Disney+'], ['prime', 'Prime Video'], ['apple', 'Apple TV+'], ['anime-airing', 'Anime airing now'], ['anime-series', 'Top anime series'], ['anime-movies', 'Top anime movies']]
/** Anime lists and lists of an added add-on have titles of their own kind, so Kind does not apply to them. */
const isAnime = (c: Catalog) => c.startsWith('anime-') || c.startsWith('addon:')
const addonKey = (a: Addon, l: Addon['lists'][number]): Catalog => `addon:${a.id}:${l.type}:${encodeURIComponent(l.id)}`
const addonLabel = (a: Addon, l: Addon['lists'][number]) => `${a.name}: ${l.name} (${l.type === 'movie' ? 'movies' : l.type === 'series' ? 'series' : 'anime'})`
const labelOf = (c: Catalog, addons: readonly Addon[]) => CATALOGS.find(([v]) => v === c)?.[1] ?? addons.flatMap((a) => a.lists.map((l) => [addonKey(a, l), addonLabel(a, l)] as const)).find(([v]) => v === c)?.[1] ?? c
/** Which built-in add-on a list comes from (Settings, Addons): removing the add-on takes its lists out of the picker. */
const groupOf = (c: Catalog): 'cinemeta' | 'streaming' | 'anime' => (c.startsWith('anime-') ? 'anime' : ['netflix', 'hbomax', 'disney', 'prime', 'apple'].includes(c) ? 'streaming' : 'cinemeta')
const OPTION = 'h-9 rounded-full border px-3.5 text-[13px] font-semibold transition-colors'

/** One filter for Discover, like the one on the Photos page: a pill with a count of what is chosen, opening a panel with the kind and the genre. */
function DiscoverFilters({ kind, setKind, genre, setGenre, catalog, setCatalog, addons, lists, off }: { kind: Kind; setKind: (k: Kind) => void; genre: string; setGenre: (g: string) => void; catalog: Catalog; setCatalog: (c: Catalog) => void; addons: readonly Addon[]; lists: typeof CATALOGS; off: boolean }) {
  const chosen = genre ? genre.split(',') : []
  const count = (kind === 'series' && !isAnime(catalog) ? 1 : 0) + chosen.length + (catalog !== 'popular' ? 1 : 0)
  const pick = (on: boolean) => cn(OPTION, on ? 'border-accent/60 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')
  return (
    <div className={cn(off && 'pointer-events-none opacity-50')} aria-disabled={off || undefined}>
      <Popover label="Filters" icon={<Icon name="filter" className="size-4" />} badge={count} active={count > 0} align="right">
        <div className="grid gap-4">
          <div className="grid gap-2">
            <h3 className="text-sm font-bold">Kind</h3>
            <div role="group" aria-label="Kind" className="flex gap-1.5">
              {([['movie', 'Movies'], ['series', 'Series']] as const).map(([v, label]) => <button key={v} type="button" disabled={isAnime(catalog)} aria-pressed={kind === v && !isAnime(catalog)} onClick={() => setKind(v)} className={cn(pick(kind === v && !isAnime(catalog)), 'disabled:opacity-40')}>{label}</button>)}
            </div>
            {isAnime(catalog) ? <p className="text-xs text-muted">This list has its own kind of titles.</p> : null}
          </div>
          {lists.length ? <div className="grid gap-2">
            <h3 className="text-sm font-bold">List</h3>
            <div role="group" aria-label="List" className="flex flex-wrap gap-1.5">
              {lists.map(([v, label]) => <button key={v} type="button" aria-pressed={catalog === v} onClick={() => setCatalog(v)} className={pick(catalog === v)}>{label}</button>)}
            </div>
          </div> : null}
          {addons.some((a) => a.lists.length) ? (
            <div className="grid gap-2">
              <h3 className="text-sm font-bold">Your add-ons</h3>
              <div role="group" aria-label="Your add-ons" className="flex flex-wrap gap-1.5">
                {addons.flatMap((a) => a.lists.map((l) => <button key={addonKey(a, l)} type="button" aria-pressed={catalog === addonKey(a, l)} onClick={() => setCatalog(addonKey(a, l))} className={pick(catalog === addonKey(a, l))}>{addonLabel(a, l)}</button>))}
              </div>
            </div>
          ) : null}
          <div className="grid gap-2">
            <h3 className="text-sm font-bold">Genre <span className="font-normal text-muted">(choose one or more)</span></h3>
            <div role="group" aria-label="Genre" className="flex flex-wrap gap-1.5">
              {['', ...GENRES].map((g) => { const on = g ? chosen.includes(g) : chosen.length === 0; return <button key={g || 'all'} type="button" aria-pressed={on} onClick={() => setGenre(g ? (chosen.includes(g) ? chosen.filter((x) => x !== g) : [...chosen, g].slice(0, 4)).join(',') : '')} className={pick(on)}>{g || 'All genres'}</button> })}
            </div>
          </div>
          {count ? <button type="button" onClick={() => { setKind('movie'); setGenre(''); setCatalog('popular') }} className="justify-self-start text-[13px] font-semibold text-muted hover:text-fg">Clear filters</button> : null}
        </div>
      </Popover>
    </div>
  )
}

/**
 * Discover: what is popular in Cinemeta's open catalogues, by kind and genre, with a search; the titles you already have are marked.
 * On a wide window the pointer resting on a poster shows that title's details in a panel beside the grid; on a narrow one a click opens them.
 */
function Discover({ text, kind, setKind, genre, setGenre, catalog, setCatalog }: { text: string; kind: Kind; setKind: (k: Kind) => void; genre: string; setGenre: (g: string) => void; catalog: Catalog; setCatalog: (c: Catalog) => void }) {
  const [open, setOpen] = useState<Found | null>(null)
  const [focus, setFocus] = useState<Found | null>(null)
  const addonsView = useAddons().data
  const addons = addonsView?.items ?? []
  const gone = (addonsView?.removed ?? []).map((r) => r.id)
  const lists = CATALOGS.filter(([v]) => !gone.includes(groupOf(v)))
  // the list that was open belongs to an add-on that is now removed: move to one that is still offered
  const offered = lists.some(([v]) => v === catalog) || catalog.startsWith('addon:')
  const first = lists[0]?.[0]
  useEffect(() => { if (addonsView && !offered && first) setCatalog(first) }, [addonsView, offered, first, setCatalog])
  const wide = useWide()
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const search = text.trim().length >= 2 ? text.trim() : ''
  const q = useInfiniteQuery({
    queryKey: ['discover', kind, genre, catalog, search] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<FoundPage>(`/api/screen/discover?type=${kind}&skip=${pageParam}${genre && !search ? `&genre=${encodeURIComponent(genre)}` : ''}${catalog !== 'popular' && !search ? `&catalog=${encodeURIComponent(catalog)}` : ''}${search ? `&search=${encodeURIComponent(search)}` : ''}`),
    getNextPageParam: (last, all) => (last.more ? (last.next ?? all.length * 50) : undefined),
    staleTime: 5 * 60_000,
    retry: false,
  })
  const items = q.data?.pages.flatMap((p) => p.items) ?? []
  const off = q.data?.pages[0]?.disabled
  const needKey = q.data?.pages[0]?.needs_key
  const shown = wide ? (items.find((x) => x.imdb === focus?.imdb && x.kind === focus.kind) ?? items[0] ?? null) : null
  const rest = (it: Found) => { clearTimeout(timer.current); timer.current = setTimeout(() => setFocus(it), 120) }
  return (
    <section aria-label="Discover" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-extrabold tracking-tight">{search ? `Results for “${search}”` : (isAnime(catalog) ? [labelOf(catalog, addons), genre.split(',').join(' · ')] : [catalog !== 'popular' ? labelOf(catalog, addons) : '', genre.split(',').join(' · ') || (catalog === 'popular' ? 'Popular' : ''), kind === 'movie' ? 'movies' : 'series']).filter(Boolean).join(' ')}</h2>
        <DiscoverFilters kind={kind} setKind={(k) => { setKind(k); setFocus(null) }} genre={genre} setGenre={(g) => { setGenre(g); setFocus(null) }} catalog={catalog} setCatalog={(c) => { setCatalog(c); setFocus(null) }} addons={addons} lists={lists} off={Boolean(search)} />
      </div>
      {off ? <p role="status" className="rounded-2xl border border-dashed border-line p-6 text-sm text-muted">Discover uses Cinemeta, the Stremio project's open catalogue, and it is switched off. Turn it on in Settings, Metadata.</p>
        : needKey ? <p role="status" className="rounded-2xl border border-dashed border-line p-6 text-sm text-muted">What is on {labelOf(catalog, addons)} comes from TMDB. Add a free TMDB key in Settings, Metadata to see it.</p>
        : q.isError ? <p role="alert" className="rounded-2xl border border-dashed border-line p-6 text-sm text-warn">The catalogue could not be reached. Check the internet connection and try again.</p>
        : q.isPending ? <p role="status" className="py-6 text-sm text-muted">Loading the catalogue…</p>
        : !items.length ? <p role="status" className="py-6 text-sm text-muted">Nothing found.</p>
        : (
          <div className={cn('grid gap-6', wide && 'grid-cols-[minmax(0,1fr)_minmax(300px,360px)]')}>
            <div className="grid content-start gap-5">
              <ul aria-label="Catalogue" className={cn('m-0 grid list-none gap-x-4 gap-y-5 p-0', wide ? 'grid-cols-[repeat(auto-fill,minmax(150px,1fr))]' : 'grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(168px,1fr))]')}>
                {items.map((it, k) => (
                  <li key={`${it.kind}:${it.imdb}`} className="mo-enter m-0" style={{ animationDelay: `${Math.min(k % 50, 14) * 25}ms` }}>
                    <PosterCard image={it.poster} title={it.title} sub={it.year ? String(it.year) : ''} rating={it.rating} active={shown?.imdb === it.imdb && shown.kind === it.kind}
                      corner={it.library ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null}
                      {...cardGo(it)} onHover={wide ? () => rest(it) : undefined} onClick={() => setOpen(it)} />
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

/** Mounts its children once it is near the screen: Home has many rows, and each asks the catalogue, so only the ones scrolled to are asked. */
function WhenNear({ children, height = 300 }: { children: ReactNode; height?: number }) {
  const box = useRef<HTMLDivElement>(null)
  const [near, setNear] = useState(typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    const el = box.current
    if (near || !el) return
    const io = new IntersectionObserver((e) => { if (e.some((x) => x.isIntersecting)) { setNear(true); io.disconnect() } }, { rootMargin: '600px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [near])
  return near ? <>{children}</> : <div ref={box} aria-hidden="true" style={{ minHeight: height }} />
}

/** A list of the open catalogue as a row: a kind, a catalogue and a genre. It leaves out by itself what has nothing to show (a switched-off add-on, a missing TMDB key). */
function CatalogRow({ title, kind, catalog = 'popular', genre = '', onSeeAll }: { title: string; kind: Kind; catalog?: Catalog; genre?: string; onSeeAll: () => void }) {
  const preview = useRowPreview()
  const q = useQuery({
    queryKey: ['discover-row', kind, catalog, genre] as const,
    queryFn: () => api<FoundPage>(`/api/screen/discover?type=${kind}&skip=0${genre ? `&genre=${encodeURIComponent(genre)}` : ''}${catalog !== 'popular' ? `&catalog=${encodeURIComponent(catalog)}` : ''}`),
    staleTime: 5 * 60_000, retry: false, select: (p) => ({ ...p, items: p.items.slice(0, 16) }),
  })
  if (!q.data?.items.length) return null
  return (
    <>
      <Row title={title} onSeeAll={onSeeAll}>
        {q.data.items.map((it) => <PosterCard key={`${it.kind}:${it.imdb}`} image={it.poster} title={it.title} sub={it.year ? String(it.year) : ''} rating={it.rating} className="w-[168px] shrink-0 snap-start" corner={it.library ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null} {...cardGo(it)} onHover={preview.hover(it)} />)}
      </Row>
      {preview.node}
    </>
  )
}

/** The rows under For you, in the order of a streaming app: what is popular, what is new, what each streaming service has, anime, genres, and the lists of the add-ons the person added. */
const HOME_ROWS: ReadonlyArray<{ title: string; kind: Kind; catalog?: Catalog; genre?: string }> = [
  { title: 'Popular movies', kind: 'movie' }, { title: 'Popular series', kind: 'series' },
  { title: 'New movies', kind: 'movie', catalog: 'new' }, { title: 'New series', kind: 'series', catalog: 'new' },
  { title: 'Featured movies', kind: 'movie', catalog: 'featured' }, { title: 'Featured series', kind: 'series', catalog: 'featured' },
  { title: 'On Netflix', kind: 'movie', catalog: 'netflix' }, { title: 'On HBO Max', kind: 'movie', catalog: 'hbomax' }, { title: 'On Disney+', kind: 'movie', catalog: 'disney' },
  { title: 'On Prime Video', kind: 'movie', catalog: 'prime' }, { title: 'On Apple TV+', kind: 'movie', catalog: 'apple' },
  { title: 'Anime airing now', kind: 'series', catalog: 'anime-airing' }, { title: 'Top anime series', kind: 'series', catalog: 'anime-series' }, { title: 'Top anime movies', kind: 'series', catalog: 'anime-movies' },
  ...(['Action', 'Comedy', 'Drama', 'Sci-Fi', 'Thriller', 'Horror', 'Animation', 'Documentary', 'Romance', 'Crime'] as const).map((g) => ({ title: `${g} movies`, kind: 'movie' as Kind, genre: g })),
  ...(['Drama', 'Comedy', 'Crime', 'Sci-Fi', 'Animation', 'Documentary'] as const).map((g) => ({ title: `${g} series`, kind: 'series' as Kind, genre: g })),
]

const WARM_UP_MS = 1500
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
  const [catalog, setCatalog] = useState<Catalog>('popular')
  const [lib, setLib] = useState<'series' | 'movies'>(view === 'series' ? 'series' : 'movies')
  const searching = text.trim().length >= 2
  const tab: HomeTab = searching ? 'discover' : view !== 'all' && stored === 'home' ? 'library' : stored
  const go = (t: HomeTab) => { setStored(t); if (t !== 'library') useScreenView.getState().setView('all') }
  const seeAll = (k: Kind, c: Catalog = 'popular', g = '') => { setKind(k); setGenre(g); setCatalog(c); go('discover') }
  const addons = useAddons()
  const navigate = useNavigate()
  /** A card of something watched through an add-on whose torrent is remembered: a plain click starts it again at once, in full screen. Otherwise (or if it cannot start) the title's own page opens. */
  const resumeAddon = (e: React.MouseEvent, a: NonNullable<Resume['addon']>) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    playInFullscreen()                                                              // asked for in this click: the browser allows it only now
    const season = a.season ?? undefined, episode = a.episode ?? undefined
    api<{ play: string; name: string; title?: string; poster?: string }>('/api/screen/stream-resume', { method: 'POST', json: { kind: a.kind, ident: a.ident, season, episode } })
      .then((r) => navigate({ to: '/addon-play', search: { src: r.play, name: r.name, title: r.title || undefined, poster: r.poster || undefined, kind: a.kind, ident: a.ident, season, episode } }))
      .catch(() => {
        cancelFullscreenPlay()
        void (season !== undefined && episode !== undefined ? navigate({ to: '/discover/$kind/$id/$season/$episode', params: { kind: a.kind, id: a.ident, season: String(season), episode: String(episode) } }) : navigate({ to: '/discover/$kind/$id', params: { kind: a.kind, id: a.ident } }))
      })
  }
  const d = home.data
  const setImmersive = useUi((x) => x.setImmersive)
  const bleed = tab === 'home' && !searching && (home.isPending || Boolean(d?.billboard.length))   // while it loads, the hero's place is kept: nothing jumps when the data arrives
  useEffect(() => { setImmersive(bleed); return () => setImmersive(false) }, [bleed, setImmersive])
  const [downloads, setDownloads] = useState(false)
  const saved = useCollection(true)
  const downloadCount = downloadsOf(saved.data?.titles ?? []).reduce((n, g) => n + g.items.length, 0)
  // The Downloads window (what is downloading and saved, what is kept, and how): a button beside the tabs, not a page of its own.
  const downloadsButton = (glass: boolean) => (
    <button type="button" onClick={() => setDownloads(true)} className={cn('inline-flex h-10 items-center gap-2 rounded-full px-4 text-sm font-semibold transition-colors', glass ? 'bg-black/50 text-white hover:bg-black/70' : 'bg-fg/10 text-fg hover:bg-fg/15')}><Icon name="download" className="size-4" />Downloads{downloadCount ? ` (${downloadCount})` : ''}</button>
  )
  const tabs = (glass: boolean) => (
    <div role="tablist" aria-label="Home" className={cn('flex w-fit gap-1 rounded-full p-1', glass ? 'bg-black/50 text-white' : 'bg-fg/10')}>
      {TABS.map(([id, label]) => (
        <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => { setText(''); go(id) }}
          className={cn('rounded-full px-5 py-2 text-sm font-semibold transition-[background-color,color] duration-300', tab === id ? (glass ? 'bg-white text-black' : 'bg-fg text-bg') : glass ? 'text-white/85 hover:text-white' : 'text-fg/80 hover:text-fg')}>{label}</button>
      ))}
    </div>
  )
  // The three parts stay mounted once opened and are only hidden: switching back shows them at once, as they were, with no refetch, no skeleton and no
  // replayed entrance. The tab pill sits at the same place in all three (over the hero on For you, under the bar on the others).
  const [seen, setSeen] = useState<ReadonlySet<HomeTab>>(() => new Set<HomeTab>([tab]))
  if (!seen.has(tab)) setSeen(new Set(seen).add(tab))
  // Once For you has settled, the other two are prepared out of sight, so the first switch to them is as instant as the later ones.
  useEffect(() => {
    if (tab !== 'home' || home.isPending || seen.size === TABS.length) return
    const t = setTimeout(() => setSeen(new Set(TABS.map(([id]) => id))), WARM_UP_MS)
    return () => clearTimeout(t)
  }, [tab, home.isPending, seen.size])
  const box = useRef<HTMLElement>(null)
  const scrolls = useRef<Partial<Record<HomeTab, number>>>({})
  const shownTab = useRef<HomeTab>(tab)
  useLayoutEffect(() => {
    const scroller = box.current?.closest('main')
    if (!scroller || shownTab.current === tab) return
    scrolls.current[shownTab.current] = scroller.scrollTop       // each part keeps its own place
    shownTab.current = tab
    scroller.scrollTop = scrolls.current[tab] ?? 0
  }, [tab])
  const part = (id: HomeTab, layout: string, body: ReactNode) => (seen.has(id) ? <div key={id} hidden={tab !== id} className={tab === id ? `mo-tab-in ${layout}` : undefined}>{body}</div> : null)
  return (
    <section ref={box} className={cn('flex w-full flex-1 flex-col', !bleed && 'px-4 pb-10 pt-4 sm:px-10')}>
      <h1 className="sr-only">{labels.videos}</h1>
      {bleed ? null : <div className="mb-6 flex flex-wrap items-center justify-between gap-3">{tabs(false)}{downloadsButton(false)}</div>}
      {part('home', bleed ? 'flex flex-col' : 'grid gap-8', (
          <>
            {bleed ? (
              <div className="relative">
                {d?.billboard.length ? <Hero items={d.billboard} /> : <div aria-hidden="true" className="-mb-28 min-h-[min(84vh,780px)] bg-surface-2" />}
                <div className="absolute inset-x-0 top-20 z-10 flex flex-wrap items-center justify-between gap-3 px-4 sm:px-10">{tabs(true)}{downloadsButton(true)}</div>
              </div>
            ) : null}
            <div className={cn('grid gap-8', bleed && 'relative z-10 px-4 pb-10 sm:px-10')}>
            {d?.continue.length ? (
              <Row title="Continue watching" hint="Where you stopped" arrowTop={80}>
                {d.continue.map((r) => {
                  const cls = 'mo-poster group relative block aspect-video w-[280px] shrink-0 snap-start overflow-hidden rounded-[18px] border border-white/10 bg-surface-2'
                  const inner = (
                    <>
                      <img src={r.image || PLACEHOLDER} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" />
                      <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-[linear-gradient(transparent,rgb(13_15_17/0.92)_70%)] p-3 pt-8">
                        <span className="truncate text-sm font-bold text-white">{r.title}</span>
                        <span className="truncate text-xs text-white/80">{[r.sub, `${r.left} min left`].filter(Boolean).join(' · ')}</span>
                      </span>
                      <span className="absolute inset-x-0 bottom-0 h-1 bg-white/25"><span className="block h-full bg-accent" style={{ width: `${r.pct}%` }} /></span>
                    </>
                  )
                  const a = r.addon
                  if (!a) return <Link key={`${r.cid}:${r.mid}`} to="/watch/$cid/$mid" onClick={playClick} params={{ cid: r.cid, mid: String(r.mid) }} aria-label={`Continue ${r.title}`} className={cls}>{inner}</Link>
                  const key = `addon:${a.kind}:${a.ident}:${a.season ?? ''}:${a.episode ?? ''}`
                  const resumeClick = r.resume ? (e: React.MouseEvent) => resumeAddon(e, a) : undefined
                  return a.season != null && a.episode != null
                    ? <Link key={key} onClick={resumeClick} to="/discover/$kind/$id/$season/$episode" params={{ kind: a.kind, id: a.ident, season: String(a.season), episode: String(a.episode) }} aria-label={`Continue ${r.title}`} className={cls}>{inner}</Link>
                    : <Link key={key} onClick={resumeClick} to="/discover/$kind/$id" params={{ kind: a.kind, id: a.ident }} aria-label={`Continue ${r.title}`} className={cls}>{inner}</Link>
                })}
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
            {HOME_ROWS.map((r) => <WhenNear key={r.title}><CatalogRow {...r} onSeeAll={() => seeAll(r.kind, r.catalog, r.genre)} /></WhenNear>)}
            {(addons.data?.items ?? []).flatMap((a) => (a.lists ?? []).slice(0, 4).map((l) => <WhenNear key={addonKey(a, l)}><CatalogRow title={`${a.name}: ${l.name}`} kind="series" catalog={addonKey(a, l)} onSeeAll={() => seeAll('series', addonKey(a, l))} /></WhenNear>))}
            </div>
          </>
      ))}
      {part('discover', 'grid gap-8', <Discover text={text} kind={kind} setKind={setKind} genre={genre} setGenre={setGenre} catalog={catalog} setCatalog={setCatalog} />)}
      {part('library', 'grid gap-8', (
          <section aria-label="Your library" className="grid gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-xl font-extrabold tracking-tight">Your library</h2>
              <div role="group" aria-label="Which part" className="flex gap-1.5">
                {([['series', 'Series'], ['movies', 'Movies']] as const).map(([v, label]) => <button key={v} type="button" aria-pressed={lib === v} onClick={() => setLib(v)} className={cn('h-9 rounded-full border px-4 text-sm font-semibold transition-colors', lib === v ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>{label}</button>)}
              </div>
            </div>
            {lib === 'series' ? <SeriesList /> : <MoviesList />}
          </section>
      ))}
      {downloads ? <DownloadsDialog open onClose={() => setDownloads(false)} /> : null}
    </section>
  )
}
