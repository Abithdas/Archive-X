import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { PLACEHOLDER } from './PosterCard'
import { Chips, TitleStage } from './TitleStage'
import { AddonExtras, AddonStreams } from './AddonStreams'
import { TrailerButton } from './TrailerButton'
import { WhereToWatch } from './WhereToWatch'
import { useHomeTab } from './view'
import { countText } from './series'

export interface FoundEpisode { episode: number; name: string; overview: string; released: string; thumb: string }
export interface FoundSeason { season: number; episodes: FoundEpisode[] }
export interface Title {
  imdb: string; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; background: string; rating: string; overview: string; genres: string[]
  library: { kind: 'tv' | 'movie'; id: number } | null; runtime: string; cast: string[]; director: string[]; trailer: string; seasons: number; episodes?: FoundSeason[]; logo?: string
}

const ID = /^(tt\d+|tmdb-\d+|kitsu:\d+)$/
const WATCH_ID = /^(tt\d+|tmdb-\d+)$/   // where-to-watch and add-on streams are asked for by IMDb or TMDB number only
const openIn = (kind: 'tv' | 'movie') => (kind === 'tv' ? '/series/$id' : '/movies/$id')
const BTN = 'inline-flex h-11 items-center gap-2 rounded-full px-6 text-sm font-bold transition-[transform,background-color,filter] active:scale-[0.97]'

/** One title of the catalogue, whether or not the library has it. */
export function useTitle(kind: 'movie' | 'series', id: string) {
  return useQuery({ queryKey: ['discover-one', kind === 'series' ? 'tv' : 'movie', id] as const, queryFn: () => api<Title>(`/api/screen/discover/${kind}/${id}`), retry: false, staleTime: 10 * 60_000, enabled: ID.test(id) })
}

const backToDiscover = () => useHomeTab.getState().setTab('discover')

function dateText(iso: string): string {
  const d = iso ? new Date(`${iso}T12:00:00`) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : ''
}

function Missing({ error }: { error: unknown }) {
  const text = error instanceof Error && error.message ? error.message : 'This title could not be loaded.'
  return <p role="alert" className="py-8 text-sm text-warn">{text}</p>
}

/**
 * The page of a movie or show that is not in your library (or is): its story, cast and trailer on the left, and on the right where to watch it, with a
 * button for each service that opens it there, and for a show its seasons with a page for every episode. Nothing is downloaded or played from here.
 */
export function DiscoverTitlePage({ kind, id }: { kind: 'movie' | 'series'; id: string }) {
  const q = useTitle(kind, id)
  const d = q.data
  const seasons = d?.episodes ?? []
  const [pick, setPick] = useState<number | null>(null)
  useEffect(() => setPick(null), [id])
  const shown = seasons.find((s) => s.season === (pick ?? seasons.find((x) => x.season > 0)?.season ?? seasons[0]?.season)) ?? null
  const lib = d?.library
  const left = q.isPending ? <p role="status" className="py-8 text-sm text-muted">Loading…</p>
    : q.isError || !d ? <Missing error={q.error} />
    : (
      <div className="mo-enter mt-auto grid max-w-2xl gap-4 pt-16">
        {d.logo ? <img src={d.logo} alt={d.title} className="max-h-28 w-auto max-w-[min(100%,420px)] object-contain object-left drop-shadow-xl" /> : <h1 className="text-[clamp(34px,5vw,64px)] font-black leading-[0.95] tracking-[-0.04em] drop-shadow-lg">{d.title}</h1>}
        <p aria-label="Facts" className="m-0 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-semibold">
          {d.year ? <span>{d.year}</span> : null}
          {d.runtime ? <span>{d.runtime}</span> : null}
          {d.kind === 'tv' && d.seasons ? <span>{countText(d.seasons, 'season')}</span> : null}
          {d.rating ? <span className="inline-flex items-center gap-1.5">{d.rating}<span className="rounded bg-amber-400 px-1 text-[11px] font-black text-black">IMDb</span></span> : null}
        </p>
        <p className="max-w-xl text-[15px] leading-relaxed text-fg/80">{d.overview || 'No description.'}</p>
        <Chips label="Genres" items={d.genres} />
        <Chips label="Cast" items={d.cast.slice(0, 6)} />
        <Chips label="Directors" items={d.director.slice(0, 3)} />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {lib ? <Link to={openIn(lib.kind)} params={{ id: String(lib.id) }} className={cn(BTN, 'bg-accent text-accent-fg hover:brightness-110')}><Icon name="play" className="size-4" />Open in your library</Link>
            : <span className={cn(BTN, 'border border-line-2 font-semibold text-muted')}>Not in your library</span>}
          <TrailerButton id={d.trailer} title={d.title} className={cn(BTN, 'border border-line-2 font-semibold hover:bg-fg/10')} />
        </div>
        <div className="grid gap-4 empty:hidden">
      {!lib && WATCH_ID.test(id) ? <WhereToWatch kind={d.kind} ident={id} title={d.title} flat /> : lib ? <p className="px-1 text-sm text-muted">You have this in your library, so there is nothing to look for elsewhere.</p> : null}
        </div>
      </div>
    )
  const panel = !d || (!seasons.length && !WATCH_ID.test(id)) ? null : (
    <div className="grid gap-5">
      {d.kind === 'movie' && WATCH_ID.test(id) ? <AddonStreams kind="movie" ident={id} /> : null}
      {WATCH_ID.test(id) && id.startsWith('tt') ? <AddonExtras kind={d.kind === 'tv' ? 'series' : 'movie'} ident={id} /> : null}
      {seasons.length ? (
        <section aria-label="Seasons and episodes" className="grid gap-2">
          <h2 className="px-1 text-lg font-bold">Episodes</h2>
          <div role="tablist" aria-label="Season" className="flex flex-wrap gap-1.5 px-1">
            {seasons.map((s) => <button key={s.season} type="button" role="tab" aria-selected={shown?.season === s.season} onClick={() => setPick(s.season)} className={cn('h-9 rounded-full border px-3.5 text-[13px] font-semibold transition-colors', shown?.season === s.season ? 'border-accent/60 bg-accent/15' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>{s.season === 0 ? 'Specials' : `Season ${s.season}`}</button>)}
          </div>
          <ul aria-label="Episodes" className="m-0 grid list-none gap-1 p-0">
            {(shown?.episodes ?? []).map((e) => (
              <li key={e.episode} className="m-0">
                <Link to="/discover/$kind/$id/$season/$episode" params={{ kind: 'series', id, season: String(shown!.season), episode: String(e.episode) }} className="group flex items-center gap-3 rounded-2xl p-2 transition-colors hover:bg-fg/10 focus-visible:bg-fg/10">
                  <span className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-lg bg-surface-3">{e.thumb ? <img src={e.thumb} alt="" loading="lazy" className="size-full object-cover" /> : <span className="grid size-full place-items-center text-xs font-bold text-faint">{shown!.season ? `E${e.episode}` : 'Special'}</span>}</span>
                  <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{shown!.season ? `${e.episode}. ` : ''}{e.name || `Episode ${e.episode}`}</span>
                    <span className="block truncate text-xs text-muted">{dateText(e.released)}</span></span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
  return <TitleStage backdrop={d ? d.background || d.poster || PLACEHOLDER : undefined} back="/videos" onBack={backToDiscover} backLabel="Back to Discover" left={left} panel={panel} panelLabel="Episodes and add-ons" />
}

/** One episode of a show: its own story and date, the episodes before and after it, and where the show can be watched. */
export function DiscoverEpisodePage({ id, season, episode }: { id: string; season: number; episode: number }) {
  const q = useTitle('series', id)
  const d = q.data
  const all = (d?.episodes ?? []).flatMap((s) => s.episodes.map((e) => ({ season: s.season, ...e })))
  const at = all.findIndex((e) => e.season === season && e.episode === episode)
  const ep = at >= 0 ? all[at] : undefined
  const prev = at > 0 ? all[at - 1] : undefined
  const next = at >= 0 ? all[at + 1] : undefined
  const to = (e: { season: number; episode: number }) => ({ kind: 'series', id, season: String(e.season), episode: String(e.episode) })
  const label = (e: { season: number; episode: number }) => (e.season === 0 ? `Special ${e.episode}` : `S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`)
  const left = q.isPending ? <p role="status" className="py-8 text-sm text-muted">Loading…</p>
    : q.isError || !d ? <Missing error={q.error} />
    : !ep ? <p role="alert" className="py-8 text-sm text-warn">This episode is not in the catalogue. <Link to="/discover/$kind/$id" params={{ kind: 'series', id }} className="font-semibold text-accent underline">Back to {d.title}</Link></p>
    : (
      <div className="mo-enter mt-auto grid max-w-2xl gap-4 pt-16">
        <Link to="/discover/$kind/$id" params={{ kind: 'series', id }} className="w-fit text-sm font-semibold text-muted transition-colors hover:text-fg">{d.title}</Link>
        <p className="m-0 text-xs font-bold uppercase tracking-[0.16em] text-fg/70">{ep.season === 0 ? 'Special' : `Season ${ep.season}`} · Episode {ep.episode}</p>
        <h1 className="text-[clamp(30px,4.6vw,56px)] font-black leading-[0.98] tracking-[-0.035em] drop-shadow-lg">{ep.name || `Episode ${ep.episode}`}</h1>
        {dateText(ep.released) ? <p className="m-0 text-sm font-semibold">First aired {dateText(ep.released)}</p> : null}
        <p className="max-w-xl text-[15px] leading-relaxed text-fg/80">{ep.overview || d.overview || 'No description.'}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {prev ? <Link to="/discover/$kind/$id/$season/$episode" params={to(prev)} className={cn(BTN, 'border border-line-2 font-semibold hover:bg-fg/10')}>← {label(prev)}</Link> : null}
          {next ? <Link to="/discover/$kind/$id/$season/$episode" params={to(next)} className={cn(BTN, 'border border-line-2 font-semibold hover:bg-fg/10')}>{label(next)} →</Link> : null}
          {d.library ? <Link to="/series/$id" params={{ id: String(d.library.id) }} className={cn(BTN, 'bg-accent text-accent-fg hover:brightness-110')}><Icon name="play" className="size-4" />Open in your library</Link> : null}
        </div>
        <div className="grid gap-4">
      {d.library ? <p className="px-1 text-sm text-muted">You have this show in your library.</p> : WATCH_ID.test(id) ? <WhereToWatch kind="tv" ident={id} title={d.title} flat /> : null}
      {!d.library ? <p className="px-1 text-xs text-faint">Streaming services list the whole show, so this is where {label(ep)} can be watched too.</p> : null}
        </div>
      </div>
    )
  const panel = !d || !ep || !WATCH_ID.test(id) ? null : (
    <div className="grid gap-3">
      <AddonStreams kind="series" ident={id} season={season} episode={episode} />
    </div>
  )
  return <TitleStage backdrop={ep?.thumb || d?.background || d?.poster || undefined} back={`/discover/series/${id}`} backLabel="Back to the show" left={left} panel={panel} panelLabel="From your add-ons" />
}
