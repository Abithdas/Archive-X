import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useEntities, usePerformer, usePerformerCareer, usePerformerMetadata, usePerformerSimilar, useSuggested } from '@/api/hooks'
import { ApiError } from '@/api/client'
import { Button } from '@/design/Button'
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton'
import { EntityEditor } from './EntityEditor'
import { NO_PERFORMER_VIDEOS } from '@/features/library/shuffle'
import { useShuffle } from '@/features/library/useShuffle'
import { VideoCard, VideoCardSkeleton } from '@/features/library/VideoCard'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { FavoriteButton } from './FavoriteButton'
import { DetailsCard, SocialPanel, SuggestionsPanel } from './StudioExtras'
import { PTABS, detailRows, isUnknownLocal, metadataLinked, similarEmpty, studioCount, studiosFor, STUDIOS_EMPTY, type PTab } from './performerextras'
import { providerPills, plural } from './studio'
import { providerBio, socialLinks } from './studioextras'
import { Link as RLink } from '@tanstack/react-router'

const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6'
const BIO_LIMIT = 280

export function PerformerPage({ id }: { id: number }) {
  const q = usePerformer(id)
  const [open, setOpen] = useState(false)
  const [imgOk, setImgOk] = useState(true)
  const shuffle = useShuffle()
  const [editing, setEditing] = useState(false)
  const [tab, setTab] = useState<PTab>('videos')
  const studios = useEntities('studios')
  const meta = usePerformerMetadata(id)
  const career = usePerformerCareer(id)
  const suggested = useSuggested('performer', id)
  const similar = usePerformerSimilar(id, tab === 'similar')

  if (q.isPending)
    return (
      <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
        <SkeletonGroup label="Loading performer" className="grid gap-6">
          <div className="flex gap-5 text-sm leading-snug">
            <Skeleton className="aspect-[3/4] w-32 rounded-card sm:w-44" />
            <div className="min-w-0 flex-1 text-3xl">
              <SkeletonLine width="45%" />
              <div className="text-sm"><SkeletonLine width="25%" /><SkeletonLine width="90%" /><SkeletonLine width="70%" /></div>
            </div>
          </div>
          <div className={GRID}>{Array.from({ length: 8 }, (_, i) => <VideoCardSkeleton key={i} />)}</div>
        </SkeletonGroup>
      </section>
    )

  if (q.isError) {
    const missing = q.error instanceof ApiError && q.error.status === 404
    return (
      <section role="alert" className="mx-auto grid max-w-xl place-items-center gap-3 p-10 text-center">
        <p className="font-semibold">{missing ? 'Performer not found.' : 'Could not load this performer.'}</p>
        {missing ? null : <Button variant="primary" onClick={() => void q.refetch()}>Retry</Button>}
        <Link to="/performers" className="font-semibold text-accent underline">Back to performers</Link>
      </section>
    )
  }

  const { performer: p, videos } = q.data
  const unknown = isUnknownLocal(p)
  const metadata = { ...(meta.data ?? { providers: [] }), career_counts: career.data?.career_counts ?? [] }
  const bio = (p.bio ?? '').trim() || (unknown ? 'Local fallback profile for videos without a confirmed performer.' : providerBio(meta.data))
  const linked = !unknown && metadataLinked(p, meta.data)
  const pills = unknown ? [] : providerPills(p.provider_ids)
  const long = bio.length > BIO_LIMIT
  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted">
        <Link to="/performers" className="font-semibold text-accent hover:underline">Performers</Link> <span aria-hidden="true">/</span> {p.name}
      </nav>
      <header className="mb-6 flex gap-5">
        <div className="aspect-[3/4] w-32 shrink-0 overflow-hidden rounded-card border border-line bg-surface-2 sm:w-44">
          {imgOk ? (
            <img src={`/api/performers/${p.id}/image?v=${encodeURIComponent(p.image ?? '')}`} alt="" onError={() => setImgOk(false)} className="h-full w-full object-cover" />
          ) : (
            <div className="grid h-full place-items-center text-5xl font-extrabold text-muted" aria-hidden="true">{p.name.charAt(0).toUpperCase()}</div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-bold uppercase tracking-wide text-faint">Performer</p>
          <h1 className="text-3xl font-extrabold tracking-tight">{p.name}</h1>
          <div className="mt-2 flex flex-wrap gap-2 text-xs font-semibold">
            {p.gender ? <span className="rounded-full bg-surface-2 px-2.5 py-1 capitalize">{p.gender}</span> : null}
            <span className="rounded-full bg-surface-2 px-2.5 py-1">{videos.length === 1 ? '1 video' : `${videos.length} videos`}</span>
            <span className={cn('rounded-full px-2.5 py-1', linked ? 'bg-good/15 text-good' : 'bg-surface-2')}>{linked ? 'Metadata linked' : 'Local profile'}</span>
            {pills.map((x) => <span key={x.label} className={cn('rounded-full px-2.5 py-1', x.tone === 'violet' ? 'bg-[#a78bfa]/15 text-[#c4b5fd]' : x.tone === 'accent' ? 'bg-accent/15 text-accent' : 'bg-surface-3 text-muted')}>{x.label}</span>)}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setEditing(true)} title="Edit performer details" aria-label="Edit performer details" className="inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5"><Icon name="edit" className="size-4" />Edit profile</button>
            <button type="button" disabled={shuffle.busy} onClick={() => void shuffle.run('mpc', () => videos, NO_PERFORMER_VIDEOS)} aria-label="Shuffle in MPC-HC" title="Play a random video of this performer in MPC-HC" className="inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60">
              <Icon name="shuffle" className="size-4" />Shuffle in MPC-HC
            </button>
            <FavoriteButton kind="performer" id={p.id} initial={Boolean(p.favorite)} />
          </div>
          <SocialPanel label="Social media links" links={socialLinks({ profile: p.profile?.startsWith('http') ? p.profile : null }, meta.data)} loading={meta.isPending && !unknown} />
          {p.profile && !p.profile.startsWith('http') ? <p className="mt-3 text-sm text-muted">{p.profile}</p> : null}
          {bio ? (
            <p className="mt-2 max-w-3xl text-sm leading-relaxed">
              {long && !open ? `${bio.slice(0, BIO_LIMIT).trimEnd()}…` : bio}{' '}
              {long ? (
                <button type="button" className="font-semibold text-accent" onClick={() => setOpen(!open)} aria-expanded={open}>
                  {open ? 'Show less' : 'Read more'}
                </button>
              ) : null}
            </p>
          ) : null}
        </div>
      </header>
      {editing ? <EntityEditor kind="performers" id={p.id} onDone={() => setEditing(false)} /> : null}
      <DetailsCard label="Performer details" rows={detailRows(p, metadata, videos.length)} related={[]} />
      <div role="tablist" aria-label="Performer details" className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {PTABS.map((t) => {
          const n = t.value === 'videos' ? videos.length : t.value === 'studios' ? studioCount(videos) : t.value === 'suggestions' ? (suggested.data?.length ?? 0) : 0
          return (
            <button key={t.value} type="button" role="tab" aria-selected={tab === t.value} onClick={() => setTab(t.value)} className={cn('-mb-px h-11 shrink-0 whitespace-nowrap border-b-2 px-3 text-sm font-bold sm:px-4', tab === t.value ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')}>
              {t.label}{n ? <span className="ml-1 text-xs font-semibold text-faint">{n}</span> : null}
            </button>
          )
        })}
      </div>
      {tab === 'videos' ? (
        videos.length ? <div className={GRID} role="tabpanel" aria-label="Videos">{videos.map((v) => <VideoCard key={`${v._cid}:${v.id}`} item={v} />)}</div> : <p role="tabpanel" aria-label="Videos" className="rounded-card border border-dashed border-line py-16 text-center text-muted">No videos for this performer yet.</p>
      ) : tab === 'studios' ? (
        (() => { const items = studiosFor(videos, studios.data ?? []); return items.length ? (
          <ul role="tabpanel" aria-label="Studios" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">{items.map((s) => <li key={s.id} className="text-center"><RLink to="/studios/$id" params={{ id: String(s.id) }} className="block rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent"><span className="grid aspect-square w-full place-items-center overflow-hidden rounded-card border border-line bg-surface-2"><img src={`/api/studios/${s.id}/image`} alt="" loading="lazy" className="size-full object-contain p-2" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /></span><span className="mt-2 block truncate text-sm font-semibold">{s.name}</span><span className="text-xs text-muted">{plural(s.count, 'video')}</span></RLink></li>)}</ul>
        ) : <p role="tabpanel" aria-label="Studios" className="rounded-card border border-dashed border-line py-12 text-center text-muted">{STUDIOS_EMPTY}</p> })()
      ) : tab === 'similar' ? (
        similar.isPending ? <p role="tabpanel" aria-label="Similar Performers" className="py-8 text-center text-sm text-muted">Finding similar performers…</p>
        : (similar.data?.items ?? []).length ? <PerformerGrid items={similar.data!.items!} />
        : <p role="tabpanel" aria-label="Similar Performers" className="rounded-card border border-dashed border-line py-12 text-center text-muted">{similarEmpty(similar.data?.reason)}</p>
      ) : <SuggestionsPanel kind="performer" entityId={p.id} name={p.name} />}
    </section>
  )
}

function PerformerGrid({ items }: { items: Array<{ id: number; name: string; video_count?: number }> }) {
  return (
    <ul role="tabpanel" aria-label="Similar Performers" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">
      {items.map((x) => <li key={x.id} className="text-center"><RLink to="/performers/$id" params={{ id: String(x.id) }} className="block rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent"><span className="relative block aspect-[3/4] w-full overflow-hidden rounded-card border border-line bg-surface-3"><img src={`/api/performers/${x.id}/image`} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /></span><span className="mt-2 block truncate text-sm font-semibold">{x.name}</span><span className="text-xs text-muted">{plural(Number(x.video_count || 0), 'video')}</span></RLink></li>)}
    </ul>
  )
}
