import { Link } from '@tanstack/react-router'
import { useReducer, useState } from 'react'
import { useEntities, usePerformer, usePerformerCareer, usePerformerMetadata, usePerformerSimilar, useSuggested } from '@/api/hooks'
import { ApiError } from '@/api/client'
import { Button } from '@/design/Button'
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton'
import { EntityEditor } from './EntityEditor'
import { ACTION, ACTION_PRIMARY, Actions, BackBar, Bio, EditingBar, Eyebrow, EntityTabs, HeroCard, MEDIA_FRAME, Pill, Pills, Title, VideosHead, providerTone } from './EntityHero'
import { PortraitAdjust } from './PortraitAdjust'
import { imageStyle, readCrop } from './portrait'
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
import { responsive } from '@/design/responsive'

const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6'
const BIO_LIMIT = 280

export function PerformerPage({ id }: { id: number }) {
  const q = usePerformer(id)
  const [imgOk, setImgOk] = useState(true)
  const shuffle = useShuffle()
  const [editing, setEditing] = useState(false)
  const [adjusting, setAdjusting] = useState(false)
  const [, refreshFraming] = useReducer((n: number) => n + 1, 0)
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
  const crop = readCrop(p.id)
  const imgSrc = `/api/performers/${p.id}/image?v=${encodeURIComponent(p.image ?? '')}`
  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <BackBar to="/performers" section="Performers" name={p.name} />
      <HeroCard label="Performer profile" media={
        <div className={cn(MEDIA_FRAME, 'group')}>
          {imgOk ? (
            <button type="button" onClick={() => setAdjusting(true)} aria-label={`Adjust portrait framing for ${p.name}`} title="Adjust portrait framing" aria-haspopup="dialog" className="absolute inset-0 block cursor-pointer outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-accent">
              <img {...responsive(imgSrc, '(max-width: 639px) 150px, 200px', [320, 480, 640, 960])} alt="" onError={() => setImgOk(false)} style={imageStyle(crop)} className="pointer-events-none size-full select-none object-cover transition-[filter] group-hover:brightness-110" />
              <span aria-hidden="true" className="pointer-events-none absolute bottom-[15px] left-1/2 inline-flex h-8 max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full border border-line-2 bg-bg/80 px-2.5 text-[11.5px] font-semibold text-fg shadow-[0_4px_15px_rgb(0_0_0/0.24)] backdrop-blur-[10px]">
                <svg viewBox="0 0 24 24" className="size-[15px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 2v14a2 2 0 0 0 2 2h14" /><path d="M18 22V8a2 2 0 0 0-2-2H2" /></svg>Adjust framing
              </span>
            </button>
          ) : (
            <div className="grid h-full place-items-center text-5xl font-extrabold text-muted" aria-hidden="true">{p.name.charAt(0).toUpperCase()}</div>
          )}
        </div>
      }>
        <Eyebrow>Performer</Eyebrow>
        <Title>{p.name}</Title>
        <Pills>
          {p.gender ? <Pill><span className="capitalize">{p.gender}</span></Pill> : null}
          <Pill>{videos.length === 1 ? '1 video' : `${videos.length} videos`}</Pill>
          <Pill tone={linked ? 'linked' : 'local'}>{linked ? 'Metadata linked' : 'Local profile'}</Pill>
          {pills.map((x) => <Pill key={x.label} tone={providerTone(x.tone)}>{x.label}</Pill>)}
        </Pills>
        {p.profile && !p.profile.startsWith('http') ? <p className="mt-2 text-[13px] text-muted">{p.profile}</p> : null}
        <Bio text={bio} limit={BIO_LIMIT} />
        <Actions>
          <button type="button" disabled={shuffle.busy} onClick={() => void shuffle.run('mpc', () => videos, NO_PERFORMER_VIDEOS)} aria-label="Shuffle in MPC-HC" title="Play a random video of this performer in MPC-HC" className={ACTION_PRIMARY}>
            <Icon name="shuffle" className="size-4" />Shuffle in MPC-HC
          </button>
          <FavoriteButton kind="performer" id={p.id} initial={Boolean(p.favorite)} />
          <button type="button" onClick={() => setEditing(true)} title="Edit performer details" aria-label="Edit performer details" className={ACTION}><Icon name="edit" className="size-4" />Edit profile</button>
        </Actions>
        <SocialPanel label="Social media links" links={socialLinks({ profile: p.profile?.startsWith('http') ? p.profile : null }, meta.data)} loading={meta.isPending && !unknown} />
      </HeroCard>
      {editing ? <EntityEditor kind="performers" id={p.id} onDone={() => setEditing(false)} /> : null}
      {adjusting ? <PortraitAdjust id={p.id} name={p.name} src={imgSrc} open onClose={() => setAdjusting(false)} onSaved={refreshFraming} /> : null}
      <DetailsCard label="Performer details" rows={detailRows(p, metadata, videos.length)} related={[]} />
      <EntityTabs label="Performer details" value={tab} onChange={setTab}
        tabs={PTABS.map((t) => ({ value: t.value, label: t.label, count: t.value === 'videos' ? videos.length : t.value === 'studios' ? studioCount(videos) : t.value === 'suggestions' ? (suggested.data?.length ?? 0) : 0 }))} />
      {tab === 'videos' ? (
        videos.length ? <><VideosHead kind="performer" videos={videos} /><div className={GRID} role="tabpanel" aria-label="Videos">{videos.map((v) => <VideoCard key={`${v._cid}:${v.id}`} item={v} />)}</div><EditingBar videos={videos} /></> : <p role="tabpanel" aria-label="Videos" className="rounded-card border border-dashed border-line py-16 text-center text-muted">No videos for this performer yet.</p>
      ) : tab === 'studios' ? (
        (() => { const items = studiosFor(videos, studios.data ?? []); return items.length ? (
          <ul role="tabpanel" aria-label="Studios" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">{items.map((s) => <li key={s.id} className="text-center"><RLink to="/studios/$id" params={{ id: String(s.id) }} className="block rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent"><span className="grid aspect-square w-full place-items-center overflow-hidden rounded-card border border-line bg-surface-2"><img {...responsive(`/api/studios/${s.id}/image`, '(max-width: 639px) 46vw, 220px', [240, 320, 480, 640])} alt="" loading="lazy" className="size-full object-contain p-2" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /></span><span className="mt-2 block truncate text-sm font-semibold">{s.name}</span><span className="text-xs text-muted">{plural(s.count, 'video')}</span></RLink></li>)}</ul>
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
      {items.map((x) => <li key={x.id} className="text-center"><RLink to="/performers/$id" params={{ id: String(x.id) }} className="block rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent"><span className="relative block aspect-[3/4] w-full overflow-hidden rounded-card border border-line bg-surface-3"><img {...responsive(`/api/performers/${x.id}/image`, '(max-width: 639px) 46vw, 220px')} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /></span><span className="mt-2 block truncate text-sm font-semibold">{x.name}</span><span className="text-xs text-muted">{plural(Number(x.video_count || 0), 'video')}</span></RLink></li>)}
    </ul>
  )
}
