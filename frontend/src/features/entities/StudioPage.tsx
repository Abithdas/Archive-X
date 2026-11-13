import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useEntityLookup, useStudio, useStudioCareer, useStudioFamily, useStudioMetadata, useStudioSuggested } from '@/api/hooks'
import { ApiError } from '@/api/client'
import { Button } from '@/design/Button'
import { Icon } from '@/design/Icon'
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { NO_STUDIO_VIDEOS } from '@/features/library/shuffle'
import { useShuffle } from '@/features/library/useShuffle'
import { VideoCard, VideoCardSkeleton } from '@/features/library/VideoCard'
import { EntityEditor } from './EntityEditor'
import { NamedPoster, PerformerPoster, PosterGrid } from './PosterCard'
import { ACTION, ACTION_PRIMARY, Actions, BackBar, Bio, EditingBar, Eyebrow, EntityTabs, HeroCard, MEDIA_FRAME, Pill, Pills, Title, VideosHead, providerTone } from './EntityHero'
import { DetailsCard, SimilarPanel, SocialPanel, SuggestionsPanel } from './StudioExtras'
import { detailRows, providerBio, relatedList, similarStudios, socialLinks } from './studioextras'
import { FavoriteButton } from './FavoriteButton'
import { NOT_FOUND, TABS, bioText, imagePath, initial, linkedPerformers, metadataStatus, plural, providerPills, BIO_LIMIT, type Tab } from './studio'
import { responsive } from '@/design/responsive'
import { Empty } from '@/design/Empty'

const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6'
const WRAP = 'mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10'

export function StudioPage({ id }: { id: number }) {
  const q = useStudio(id)
  const performers = useEntityLookup('performers', (q.data?.videos ?? []).flatMap((v) => (Array.isArray(v.performers) ? v.performers : [])))
  const studios = useEntityLookup('studios', (q.data?.videos ?? []).flatMap((v) => (Array.isArray(v.studios) ? v.studios : [])))
  const meta = useStudioMetadata(id)
  const family = useStudioFamily(id)
  const career = useStudioCareer(id)
  const suggested = useStudioSuggested(id)
  const [tab, setTab] = useState<Tab>('videos')
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
      <BackBar to="/studios" section="Studios" name={site.name} />
      <HeroCard label="Studio profile" media={
        <div className={cn(MEDIA_FRAME, 'grid place-items-center')}>
          {logoOk ? <img {...responsive(imagePath(site.id, site.image), '(max-width: 639px) 150px, 200px', [320, 480, 640])} alt="" onError={() => setLogoOk(false)} className="size-full object-contain p-3.5" /> : <span className="text-5xl font-extrabold text-muted" aria-hidden="true">{initial(site.name)}</span>}
        </div>
      }>
        <Eyebrow>Studio</Eyebrow>
        <Title>{site.name}</Title>
        <Pills>
          <Pill>{plural(videos.length, 'video')}</Pill>
          <Pill>{plural(linked.length, 'performer')}</Pill>
          <Pill tone={metadataStatus(site) === 'Metadata linked' ? 'linked' : 'local'}>{metadataStatus(site)}</Pill>
          {pills.map((p) => <Pill key={p.label} tone={providerTone(p.tone)}>{p.label}</Pill>)}
        </Pills>
        <Bio text={bio} limit={BIO_LIMIT} />
        <Actions>
          <button type="button" disabled={shuffle.busy} onClick={() => void shuffle.run('mpc', () => videos, NO_STUDIO_VIDEOS)} aria-label="Shuffle in MPC-HC" title="Play a random video from this studio in MPC-HC" className={ACTION_PRIMARY}><Icon name="shuffle" className="size-4" />Shuffle in MPC-HC</button>
          <FavoriteButton kind="studio" id={site.id} initial={Boolean(site.favorite)} />
          <button type="button" onClick={() => setEditing(true)} title="Edit studio details" aria-label="Edit studio details" className={ACTION}><Icon name="edit" className="size-4" />Edit studio</button>
        </Actions>
        <SocialPanel links={socialLinks(site, meta.data)} loading={meta.isPending} />
      </HeroCard>

      <DetailsCard rows={detailRows(site, metadata, videos.length, linked.length)} related={relatedList(family.data?.related)} />

      <EntityTabs label="Studio details" value={tab} onChange={setTab}
        tabs={TABS.map((t) => ({ value: t.value, label: t.label, count: t.value === 'videos' ? videos.length : t.value === 'performers' ? linked.length : t.value === 'suggestions' ? (suggested.data?.length ?? 0) : 0 }))} />

      {tab === 'similar' ? <SimilarPanel items={similarStudios(videos, site.name, studios.data ?? [])} /> : tab === 'suggestions' ? <SuggestionsPanel kind="studio" entityId={site.id} name={site.name} /> : tab === 'videos' ? (
        videos.length ? <><VideosHead kind="studio" videos={videos} /><div className={GRID} role="tabpanel" aria-label="Videos">{videos.map((v) => <VideoCard key={`${v._cid}:${v.id}`} item={v} />)}</div><EditingBar videos={videos} /></> : <Empty role="tabpanel" aria-label="Videos" icon="library">No videos are linked to this studio yet.</Empty>
      ) : linked.length ? (
        <PosterGrid label="Performers">{linked.map((p) => { const pid = idOf(p.name); return pid ? <PerformerPoster key={p.name} id={pid} name={p.name} count={p.count} /> : <NamedPoster key={p.name} name={p.name} count={p.count} /> })}</PosterGrid>
      ) : <Empty role="tabpanel" aria-label="Performers" icon="user">No performers are linked to this studio&apos;s videos yet.</Empty>}

      {editing ? <EntityEditor kind="studios" id={site.id} onDone={() => setEditing(false)} /> : null}
    </section>
  )
}
