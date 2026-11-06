import { Link, useNavigate, useRouter } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, thumbUrl } from '@/api/client'
import { useEntities, useFavorite, useLibrary, useMarkers, usePreviewManifest, useVideo } from '@/api/hooks'
import type { LibraryItem } from '@/api/types'
import { Icon } from '@/design/Icon'
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { formatBytes } from '@/design/format'
import { openExternal } from '@/features/library/CardMenu'
import { formatDuration } from '@/features/library/format'
import { useLibraryStore } from '@/features/library/store'
import { Player } from './Player'
import { normaliseChapters, toSegments } from './timeline'

const CARD = 'rounded-[18px] border border-line bg-surface p-4'
const CHIP = 'inline-flex h-7 items-center rounded-full bg-surface-3 px-3 text-xs font-semibold text-muted'

/** Telegram deep link for a message in a channel (-100… ids are public-link channel ids). */
export const telegramLink = (cid: string, mid: number) => (/^-100\d+$/.test(cid) ? `https://t.me/c/${cid.slice(4)}/${mid}` : null)

function Skel() {
  return (
    <SkeletonGroup label="Loading video" className="mx-auto grid w-full max-w-[1500px] gap-4 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="grid gap-4 text-sm leading-snug">
        <Skeleton className="aspect-video w-full rounded-[18px]" />
        <div className="text-3xl"><SkeletonLine width="55%" /></div>
        <SkeletonLine width="30%" />
        <Skeleton className="h-40 w-full rounded-[18px]" />
      </div>
      <Skeleton className="hidden h-96 rounded-[18px] lg:block" />
    </SkeletonGroup>
  )
}

function UpNext({ items, current }: { items: LibraryItem[]; current: LibraryItem }) {
  const rest = items.filter((i) => !(i.id === current.id && i._cid === current._cid)).slice(0, 10)
  return (
    <section aria-label="Up next videos" className={cn(CARD, 'self-start')}>
      <div className="mb-3 flex items-baseline justify-between"><h2 className="text-lg font-extrabold">Up next</h2><span className="text-xs text-faint">Current queue</span></div>
      <ul className="grid gap-2.5">
        {rest.map((i) => (
          <li key={`${i._cid}:${i.id}`}>
            <Link to="/watch/$cid/$mid" params={{ cid: i._cid, mid: String(i.id) }} className="flex gap-3 rounded-xl p-1 hover:bg-fg/5">
              <span className="relative aspect-video w-[124px] shrink-0 overflow-hidden rounded-lg bg-surface-2">
                <img src={thumbUrl(i._cid, i.thumbnail_id)} alt="" loading="lazy" className="h-full w-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                {i.duration ? <span className="absolute bottom-1 right-1 rounded-md bg-black/65 px-1.5 text-[11px] font-semibold text-white">{formatDuration(i.duration)}</span> : null}
              </span>
              <span className="line-clamp-3 min-w-0 self-center text-[13px] font-semibold leading-snug [overflow-wrap:anywhere]">{i.name}</span>
            </Link>
          </li>
        ))}
        {!rest.length ? <li className="text-sm text-muted">Nothing else in this queue.</li> : null}
      </ul>
    </section>
  )
}

export function WatchPage({ cid, mid }: { cid: string; mid: number }) {
  const video = useVideo(cid, mid)
  const router = useRouter()
  const navigate = useNavigate()
  const { channels, sort } = useLibraryStore()
  const queue = useLibrary({ channels: channels.length ? channels : [cid], page: 0, per: 24, sort })
  const markers = useMarkers(video.data?.media_id)
  const manifest = usePreviewManifest(cid, mid)
  const performers = useEntities('performers')
  const studios = useEntities('studios')
  const fav = useFavorite()
  const [showTags, setShowTags] = useState(false)
  const item = video.data
  const items = queue.data?.items ?? []
  const index = item ? items.findIndex((i) => i.id === item.id && i._cid === item._cid) : -1
  const go = (i?: LibraryItem) => i && void navigate({ to: '/watch/$cid/$mid', params: { cid: i._cid, mid: String(i.id) } })
  const prev = index > 0 ? items[index - 1] : undefined
  const next = index >= 0 ? items[index + 1] : items.find((i) => item && !(i.id === item.id && i._cid === item._cid))
  const segments = useMemo(() => toSegments(normaliseChapters(markers.data, item?.duration ?? 0), item?.duration ?? 0), [markers.data, item?.duration])
  const preview = manifest.data?.frames_ready && manifest.data.frame_times.length ? { times: manifest.data.frame_times, url: (n: number) => `/api/play/preview-frame/${encodeURIComponent(cid)}/${mid}/${n}` } : undefined
  const idOf = (list: Array<{ id: number; name: string }> | undefined, name: string) => list?.find((e) => e.name.toLowerCase() === name.toLowerCase())?.id

  if (video.isPending) return <Skel />
  if (video.isError || !item) {
    const missing = video.error instanceof ApiError && video.error.status === 404
    return (
      <section role="alert" className="mx-auto grid max-w-xl place-items-center gap-3 p-10 text-center">
        <p className="font-semibold">{missing ? 'Video not found.' : 'Could not load this video.'}</p>
        {missing ? null : <button type="button" onClick={() => void video.refetch()} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Retry</button>}
        <Link to="/" className="font-semibold text-accent underline">Back to videos</Link>
      </section>
    )
  }

  const tg = telegramLink(item._cid, item.id)
  const studio = item.studio || item.studios[0] || ''
  const vlc = () => void openExternal(item, 'vlc').then((r) => toast(`Opened in ${r.player ?? 'VLC'}`)).catch((e) => toast.error(`Player error: ${e instanceof ApiError ? e.detail : 'not found'}`))
  const mpc = () => void openExternal(item, 'mpc').then((r) => toast(`Opened in ${r.player ?? 'MPC-HC'}`)).catch((e) => toast.error(`Player error: ${e instanceof ApiError ? e.detail : 'not found'}`))
  const tags = showTags ? item.tags : item.tags.slice(0, 12)

  return (
    <div className="mx-auto grid w-full max-w-[1500px] gap-5 px-4 pb-10 pt-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="grid min-w-0 content-start gap-4">
        <div className="flex items-center justify-between gap-2">
          <button type="button" onClick={() => router.history.back()} aria-label="Go back" title="Go back" className="grid size-10 place-items-center rounded-xl border border-line bg-surface hover:bg-surface-2"><Icon name="arrow-left" /></button>
          {tg ? <a href={tg} target="_blank" rel="noreferrer" className="inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold hover:bg-surface-2"><Icon name="brand-telegram" />Telegram</a> : null}
        </div>

        <Player src={`/stream/${encodeURIComponent(item._cid)}/${item.id}`} durationHint={item.duration} segments={segments} preview={preview} onPrev={prev ? () => go(prev) : undefined} onNext={next ? () => go(next) : undefined} onOpenVlc={vlc} />

        <header className="min-w-0">
          <h1 className="text-[clamp(22px,2.4vw,30px)] font-extrabold leading-tight tracking-tight [overflow-wrap:anywhere]">{item.name}</h1>
          <p className="mt-1 truncate text-xs text-faint">Telegram filename: {item.filename}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {item.resolution_category ? <span className={cn(CHIP, 'bg-good/15 text-good')}>{item.resolution_category}</span> : null}
            {item.duration ? <span className={CHIP}>{formatDuration(item.duration)}</span> : null}
            {item.size ? <span className={CHIP}>{formatBytes(item.size)}</span> : null}
            {item.codec ? <span className={CHIP}>{item.codec}</span> : null}
            {studio ? <span className={cn(CHIP, 'gap-2')}><b className="text-fg">{studio}</b></span> : null}
          </div>
        </header>

        <section className={CARD} aria-label="Performers and studio">
          <h2 className="text-base font-bold">Performers &amp; Studio</h2>
          <p className="mb-3 text-xs text-faint">People and studio linked to this video</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-semibold text-faint">Performers</p>
              <ul className="grid gap-2">
                {item.performers.map((n) => {
                  const id = idOf(performers.data, n)
                  const inner = (
                    <>
                      <span className="size-9 shrink-0 overflow-hidden rounded-full bg-surface-3">
                        {id ? <img src={`/api/performers/${id}/image`} alt="" className="size-full object-cover" onError={(e) => (e.currentTarget.src = '/static/icons/ax-placeholder.png')} /> : <img src="/static/icons/ax-placeholder.png" alt="" className="size-full object-cover" />}
                      </span>
                      <span className="min-w-0 text-left"><span className="block truncate text-sm font-bold">{n}</span><span className="text-xs text-faint">Performer</span></span>
                    </>
                  )
                  return <li key={n}>{id ? <Link to="/performers/$id" params={{ id: String(id) }} className="flex items-center gap-3 rounded-xl border border-line bg-bg p-2 hover:bg-surface-2">{inner}</Link> : <div className="flex items-center gap-3 rounded-xl border border-line bg-bg p-2">{inner}</div>}</li>
                })}
                {!item.performers.length ? <li className="text-sm text-muted">Unknown performer</li> : null}
              </ul>
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold text-faint">Studio</p>
              {studio ? (
                <div className="flex items-center gap-3 rounded-xl border border-line bg-bg p-2">
                  <span className="size-9 shrink-0 overflow-hidden rounded-lg bg-surface-3">
                    <img src={idOf(studios.data, studio) ? `/api/studios/${idOf(studios.data, studio)}/image` : '/static/icons/ax-placeholder.png'} alt="" className="size-full object-cover" onError={(e) => (e.currentTarget.src = '/static/icons/ax-placeholder.png')} />
                  </span>
                  <span className="min-w-0"><span className="block truncate text-sm font-bold">{studio}</span><span className="text-xs uppercase text-faint">Studio</span></span>
                </div>
              ) : <p className="text-sm text-muted">No studio</p>}
            </div>
          </div>
        </section>

        {item.tags.length ? (
          <section className={CARD} aria-label="Scene tags">
            <h2 className="mb-2 text-base font-bold">Scene tags</h2>
            <ul className="flex flex-wrap gap-1.5">{tags.map((t) => <li key={t} className="rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-semibold text-accent">{t}</li>)}</ul>
            {item.tags.length > 12 ? <button type="button" onClick={() => setShowTags(!showTags)} aria-expanded={showTags} className="mt-2 text-xs font-semibold text-muted hover:text-fg">{showTags ? 'Show fewer' : `Show all ${item.tags.length}`}</button> : null}
          </section>
        ) : null}

        <section className={CARD} aria-label="Source">
          <h2 className="mb-1 text-base font-bold">Source</h2>
          <p className="text-sm text-muted">{item.channel_title ?? 'Channel'} · Telegram message {item.id}</p>
        </section>

        <section className={CARD} aria-label="Actions">
          <h2 className="mb-3 text-base font-bold">Actions</h2>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={vlc} className="inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-3.5 text-sm font-semibold hover:bg-fg/5"><Icon name="brand-vlc" />Open in VLC</button>
            <button type="button" onClick={mpc} className="inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-3.5 text-sm font-semibold hover:bg-fg/5"><Icon name="play" />Open in MPC-HC</button>
            <button type="button" aria-pressed={Boolean(item.favorite)} disabled={fav.isPending} onClick={() => fav.mutate({ mediaId: item.media_id, favorite: !item.favorite }, { onError: () => toast.error('Could not update favourite') })} className={cn('inline-flex h-10 items-center gap-2 rounded-xl border px-3.5 text-sm font-semibold hover:bg-fg/5', item.favorite ? 'border-warn/60 text-warn' : 'border-line-2')}>
              <Icon name="star" />{item.favorite ? 'Favourited' : 'Favourite'}
            </button>
          </div>
        </section>
      </div>
      <UpNext items={items} current={item} />
    </div>
  )
}
