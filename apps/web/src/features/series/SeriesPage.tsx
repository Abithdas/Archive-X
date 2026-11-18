import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { api } from '@/api/client'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { episodeLabel, countText, lengthText, seasonSummary, type SeriesCard, type SeriesDetail } from './series'

const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/** The TV shows in the library: a poster for each, with how many episodes are here. */
export function SeriesPage() {
  const q = useQuery({ queryKey: ['series'], queryFn: () => api<{ items: SeriesCard[] }>('/api/screen/series') })
  const items = q.data?.items ?? []
  return (
    <div className="px-4 py-5 md:px-8">
      <PageHead title="Series" blurb="Your TV shows, season by season" />
      {q.isPending ? <p role="status" className="py-8 text-center text-sm text-muted">Loading shows…</p>
        : q.isError ? <p role="alert" className="py-8 text-center text-sm text-warn">Could not load the shows.</p>
        : !items.length ? <Empty icon="library" title="No shows yet">Add a folder of TV shows in Settings → Local files (marked SFW), then identify it in Settings → Metadata.</Empty>
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
        )}
    </div>
  )
}

/** One show: the story, the cast, and the episodes in the library, season by season. */
export function SeriesDetailPage({ id }: { id: number }) {
  const q = useQuery({ queryKey: ['series', id], queryFn: () => api<SeriesDetail>(`/api/screen/series/${id}`) })
  const d = q.data
  return (
    <div className="px-4 py-5 md:px-8">
      <Link to="/series" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-muted hover:text-fg"><Icon name="arrow-left" className="size-4" />Series</Link>
      {q.isPending ? <p role="status" className="py-8 text-center text-sm text-muted">Loading…</p>
        : q.isError || !d ? <p role="alert" className="py-8 text-center text-sm text-warn">This show is not in the library.</p>
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
            {d.seasons.length ? d.seasons.map((s) => (
              <section key={s.season} aria-label={s.name} className="mb-6">
                <h2 className="mb-2 flex items-baseline gap-3 text-lg font-bold">{s.name}<span className="text-xs font-medium text-faint">{seasonSummary(s)}</span></h2>
                <ul className="m-0 grid list-none gap-2 p-0">
                  {s.episodes.map((e) => (
                    <li key={`${e.cid}:${e.mid}`} className="m-0">
                      <Link to="/watch/$cid/$mid" params={{ cid: e.cid, mid: String(e.mid) }} className="ax-lift flex gap-3 rounded-2xl border border-line bg-surface p-3 hover:bg-fg/5">
                        <span className="w-16 shrink-0 pt-0.5 text-xs font-bold text-accent">{episodeLabel(s.season, e)}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-semibold">{e.name}</span>
                          {e.overview ? <span className="line-clamp-2 text-sm text-muted">{e.overview}</span> : null}
                        </span>
                        <span className="shrink-0 text-xs text-faint">{[e.air_date, lengthText(e)].filter(Boolean).join(' · ')}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            )) : <Empty icon="library" title="No episodes in the library">Episodes appear here once their files are in an SFW folder and identified.</Empty>}
          </>
        )}
    </div>
  )
}
