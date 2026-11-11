import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { usePerformer } from '@/api/hooks'
import { ApiError } from '@/api/client'
import { Button } from '@/design/Button'
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton'
import { NO_PERFORMER_VIDEOS } from '@/features/library/shuffle'
import { useShuffle } from '@/features/library/useShuffle'
import { VideoCard, VideoCardSkeleton } from '@/features/library/VideoCard'
import { Icon } from '@/design/Icon'

const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6'
const BIO_LIMIT = 280

export function PerformerPage({ id }: { id: number }) {
  const q = usePerformer(id)
  const [open, setOpen] = useState(false)
  const [imgOk, setImgOk] = useState(true)
  const shuffle = useShuffle()

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
  const bio = (p.bio ?? '').trim()
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
          <h1 className="text-3xl font-extrabold tracking-tight">{p.name}</h1>
          <div className="mt-2 flex flex-wrap gap-2 text-xs font-semibold">
            {p.gender ? <span className="rounded-full bg-surface-2 px-2.5 py-1 capitalize">{p.gender}</span> : null}
            <span className="rounded-full bg-surface-2 px-2.5 py-1">{videos.length === 1 ? '1 video' : `${videos.length} videos`}</span>
            {p.favorite ? <span className="rounded-full bg-accent/20 px-2.5 py-1 text-accent">Favourite</span> : null}
          </div>
          <div className="mt-3">
            <button type="button" disabled={shuffle.busy} onClick={() => void shuffle.run('mpc', () => videos, NO_PERFORMER_VIDEOS)} aria-label="Shuffle in MPC-HC" title="Play a random video of this performer in MPC-HC" className="inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60">
              <Icon name="shuffle" className="size-4" />Shuffle in MPC-HC
            </button>
          </div>
          {p.profile ? <p className="mt-3 text-sm text-muted">{p.profile}</p> : null}
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
      {videos.length ? (
        <div className={GRID}>{videos.map((v) => <VideoCard key={`${v._cid}:${v.id}`} item={v} />)}</div>
      ) : (
        <p className="rounded-card border border-dashed border-line py-16 text-center text-muted">No videos for this performer yet.</p>
      )}
    </section>
  )
}
