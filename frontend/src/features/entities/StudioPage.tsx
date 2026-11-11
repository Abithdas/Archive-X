import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useEntities, useStudio, useStudioCareer, useStudioFamily, useStudioMetadata, useStudioSuggested } from '@/api/hooks'
import { ApiError } from '@/api/client'
import { Button } from '@/design/Button'
import { Icon } from '@/design/Icon'
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { NO_STUDIO_VIDEOS } from '@/features/library/shuffle'
import { useShuffle } from '@/features/library/useShuffle'
import { VideoCard, VideoCardSkeleton } from '@/features/library/VideoCard'
import { EntityEditor } from './EntityEditor'
import { DetailsCard, SimilarPanel, SocialPanel, SuggestionsPanel } from './StudioExtras'
import { detailRows, providerBio, relatedList, similarStudios, socialLinks } from './studioextras'
import { FavoriteButton } from './FavoriteButton'
import { NOT_FOUND, TABS, bioShown, bioText, imagePath, initial, linkedPerformers, metadataStatus, plural, providerPills, BIO_LIMIT, type Tab } from './studio'

const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6'
const WRAP = 'mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10'
const PILL = 'rounded-full bg-surface-2 px-2.5 py-1'
const PILL_TONE = { violet: 'bg-[#a78bfa]/15 text-[#c4b5fd]', accent: 'bg-accent/15 text-accent', plain: 'bg-surface-3 text-muted' } as const

export function StudioPage({ id }: { id: number }) {
  const q = useStudio(id)
  const performers = useEntities('performers')
  const studios = useEntities('studios')
  const meta = useStudioMetadata(id)
  const family = useStudioFamily(id)
  const career = useStudioCareer(id)
  const suggested = useStudioSuggested(id)
  const [tab, setTab] = useState<Tab>('videos')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [logoOk, setLogoOk] = useState(true)
  const shuffle = useShuffle()

  if (q.isPending)
    return (
      <section className={WRAP}>
        <SkeletonGroup label="Loading studio" className="grid gap-6">
          <div className="flex gap-5 text-sm leading-snug">
            <Skeleton className="size-28 rounded-card sm:size-40" />
            <div className="min-w-0 flex-1 text-3xl"><SkeletonLine width="40%" /><div className="text-sm"><SkeletonLine width="25%" /><SkeletonLine width="85%" /></div></div>
          </div>
          <div className={GRID}>{Array.from({ length: 8 }, (_, i) => <VideoCardSkeleton key={i} />)}</div>
        </SkeletonGroup>
      </section>
    )
  if (q.isError) {
    const missing = q.error instanceof ApiError && q.error.status === 404
    return (
      <section className={cn(WRAP, 'grid place-items-center gap-3 text-center')} role="alert">
        <p className="font-semibold">{missing ? NOT_FOUND : 'Could not load this studio.'}</p>
        {missing ? null : <Button variant="primary" onClick={() => void q.refetch()}>Retry</Button>}
        <Link to="/studios" className="font-semibold text-accent underline">Back to studios</Link>
      </section>
    )
  }

  const { site, videos } = q.data
  const linked = linkedPerformers(videos)
  const metadata = { ...(meta.data ?? { providers: [] }), career_counts: career.data?.career_counts ?? [] }
  const bio = bioText(site) || providerBio(meta.data)
  const pills = providerPills(site.provider_ids)
  const idOf = (name: string) => performers.data?.find((p) => p.name.toLowerCase() === name.toLowerCase())?.id
  return (
    <section className={WRAP}>
      <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted"><Link to="/studios" className="font-semibold text-accent hover:underline">Studios</Link> <span aria-hidden="true">/</span> {site.name}</nav>
      <header className="mb-6 flex gap-5" aria-label="Studio profile">
        <div className="grid size-28 shrink-0 place-items-center overflow-hidden rounded-card border border-line bg-surface-2 sm:size-40">
          {logoOk ? <img src={imagePath(site.id, site.image)} alt="" onError={() => setLogoOk(false)} className="size-full object-contain p-2" /> : <span className="text-5xl font-extrabold text-muted" aria-hidden="true">{initial(site.name)}</span>}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-bold uppercase tracking-wide text-faint">Studio</p>
          <h1 className="text-3xl font-extrabold tracking-tight [overflow-wrap:anywhere]">{site.name}</h1>
          <div className="mt-2 flex flex-wrap gap-2 text-xs font-semibold">
            <span className={PILL}>{plural(videos.length, 'video')}</span>
            <span className={PILL}>{plural(linked.length, 'performer')}</span>
            <span className={cn(PILL, metadataStatus(site) === 'Metadata linked' && 'bg-good/15 text-good')}>{metadataStatus(site)}</span>
            {pills.map((p) => <span key={p.label} className={cn('rounded-full px-2.5 py-1', PILL_TONE[p.tone])}>{p.label}</span>)}
          </div>
          {bio ? (
            <p className="mt-3 max-w-3xl text-sm leading-relaxed">
              {bioShown(bio, open)}{' '}
              {bio.length > BIO_LIMIT ? <button type="button" className="font-semibold text-accent" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Show less' : 'Read more'}</button> : null}
            </p>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" disabled={shuffle.busy} onClick={() => void shuffle.run('mpc', () => videos, NO_STUDIO_VIDEOS)} aria-label="Shuffle in MPC-HC" title="Play a random video from this studio in MPC-HC" className="inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60"><Icon name="shuffle" className="size-4" />Shuffle in MPC-HC</button>
            <FavoriteButton kind="studio" id={site.id} initial={Boolean(site.favorite)} />
            <button type="button" onClick={() => setEditing(true)} title="Edit studio details" aria-label="Edit studio details" className="inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5"><Icon name="edit" className="size-4" />Edit studio</button>
          </div>
          <SocialPanel links={socialLinks(site, meta.data)} loading={meta.isPending} />
        </div>
      </header>

      <DetailsCard rows={detailRows(site, metadata, videos.length, linked.length)} related={relatedList(family.data?.related)} />

      <div role="tablist" aria-label="Studio details" className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button key={t.value} type="button" role="tab" aria-selected={tab === t.value} onClick={() => setTab(t.value)} className={cn('-mb-px h-11 shrink-0 whitespace-nowrap border-b-2 px-3 text-sm font-bold sm:px-4', tab === t.value ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')}>
            {t.label}{t.value === 'videos' || t.value === 'performers' || (t.value === 'suggestions' && suggested.data?.length) ? <span className="ml-1 text-xs font-semibold text-faint">{t.value === 'videos' ? videos.length : t.value === 'performers' ? linked.length : suggested.data?.length}</span> : null}
          </button>
        ))}
      </div>

      {tab === 'similar' ? <SimilarPanel items={similarStudios(videos, site.name, studios.data ?? [])} /> : tab === 'suggestions' ? <SuggestionsPanel studioId={site.id} name={site.name} /> : tab === 'videos' ? (
        videos.length ? <div className={GRID} role="tabpanel" aria-label="Videos">{videos.map((v) => <VideoCard key={`${v._cid}:${v.id}`} item={v} />)}</div> : <p role="tabpanel" aria-label="Videos" className="rounded-card border border-dashed border-line py-12 text-center text-muted">No videos are linked to this studio yet.</p>
      ) : linked.length ? (
        <ul role="tabpanel" aria-label="Performers" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">
          {linked.map((p) => {
            const pid = idOf(p.name)
            const inner = (
              <>
                <span className="relative block aspect-[3/4] w-full overflow-hidden rounded-card border border-line bg-surface-3">
                  {pid ? <img src={`/api/performers/${pid}/image`} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /> : <span className="absolute inset-0 grid place-items-center text-4xl font-extrabold text-muted" aria-hidden="true">{initial(p.name)}</span>}
                </span>
                <span className="mt-2 block truncate text-sm font-semibold">{p.name}</span>
                <span className="text-xs text-muted">{plural(p.count, 'video')}</span>
              </>
            )
            return <li key={p.name} className="text-center">{pid ? <Link to="/performers/$id" params={{ id: String(pid) }} className="block rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent">{inner}</Link> : <div>{inner}</div>}</li>
          })}
        </ul>
      ) : <p role="tabpanel" aria-label="Performers" className="rounded-card border border-dashed border-line py-12 text-center text-muted">No performers are linked to this studio&apos;s videos yet.</p>}

      {editing ? <EntityEditor kind="studios" id={site.id} onDone={() => setEditing(false)} /> : null}
    </section>
  )
}
