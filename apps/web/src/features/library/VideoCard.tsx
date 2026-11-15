import { Link } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { thumbUrl } from '@/api/client'
import { useFavorite } from '@/api/hooks'
import type { LibraryItem } from '@/api/types'
import { LikeHeart } from '@/design/LikeHeart'
import { Icon } from '@/design/Icon'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { CardMenu } from './CardMenu'
import { formatDuration } from './format'
import { pickKey, useSelection } from './selection'
import { useCardPreview } from './useCardPreview'
import { responsive } from '@/design/responsive'

/** Card metrics live in one place so the skeleton below can never drift from the real card (values from the classic UI). */
const CARD = '@container relative overflow-hidden rounded-card border border-line bg-surface'
const THUMB = 'relative aspect-video w-full overflow-hidden bg-surface-2'
const BODY = 'grid grid-cols-[minmax(0,1fr)] px-3 pb-3 pt-2.5'
const BADGE = 'absolute rounded-[7px] bg-black/65 px-[7px] py-px text-[11.5px] font-semibold leading-[1.45] text-white'
const TAG_TINTS = ['bg-accent/15 text-accent', 'bg-[#a78bfa]/15 text-[#c4b5fd]', 'bg-surface-3 text-muted']

const initials = (name: string) => name.trim().charAt(0).toUpperCase()

export function VideoCard({ item }: { item: LibraryItem }) {
  const [imgOk, setImgOk] = useState(true)
  const fav = useFavorite()
  const thumb = useRef<HTMLDivElement>(null)
  const preview = useCardPreview(item._cid, item.id, Number(item.duration) || 0, thumb)
  const duration = formatDuration(preview.seconds ?? item.duration)
  const people = item.performers
  const place = [item.studio, item.channel_title].filter(Boolean).join(' · ')
  const selecting = useSelection((s) => s.active)
  const picked = useSelection((s) => Boolean(s.picked[pickKey(item._cid, item.id)]))
  const toggle = useSelection((s) => s.toggle)
  // In select mode a click on the card picks it instead of opening it; the favourite and ⋯ buttons keep working.
  const pick = (e: React.MouseEvent) => {
    if (!selecting || (e.target as HTMLElement).closest('button,input')) return
    e.preventDefault()
    e.stopPropagation()
    toggle(item._cid, item.id)
  }
  return (
    <article {...preview.bind} data-glow data-smoke={`card:${item._cid}:${item.id}`} onClickCapture={pick} data-picked={selecting ? picked : undefined} className={cn(CARD, 'transform-gpu transition-[box-shadow,translate,scale] duration-200 ease-[cubic-bezier(.2,.75,.25,1)] will-change-[translate,scale] motion-reduce:transition-none hover:z-40 hover:-translate-y-[3px] hover:scale-[1.025] hover:shadow-[0_12px_26px_rgb(0_0_0/0.24)]', selecting && 'cursor-pointer', picked && 'border-accent ring-2 ring-accent/60')} aria-label={item.name}>
      <div ref={thumb} className={THUMB}>
        {imgOk ? (
          <img
            {...responsive(thumbUrl(item._cid, item.thumbnail_id), '(max-width: 519px) calc(100vw - 32px), (max-width: 899px) 46vw, (max-width: 1199px) 30vw, (max-width: 1699px) 23vw, 18vw', [320, 480, 640], 640)}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setImgOk(false)}
            className="absolute inset-0 h-full w-full object-cover"
          />
        ) : null}
        {preview.frame ? <img src={preview.frame} alt="" aria-hidden="true" draggable={false} className="absolute inset-0 z-[1] size-full object-cover" /> : null}
        {selecting ? (
          <input type="checkbox" checked={picked} onChange={() => toggle(item._cid, item.id)} aria-label={`Select ${item.name}`} className="absolute left-2 top-2 z-10 size-5 cursor-pointer accent-[var(--color-accent)]" />
        ) : null}
        {item.resolution_category ? <span className={cn(BADGE, 'bottom-2 left-2 z-[2]')}>{item.resolution_category}</span> : null}
        {duration ? <span className={cn(BADGE, 'bottom-2 right-2 z-[2]')}>{duration}</span> : null}
        {preview.loading ? <Skeleton className="absolute inset-x-0 bottom-0 z-[2] h-[3px] rounded-none" /> : null}
        {preview.ratio !== null || preview.watched > 0 ? (
          <span role="progressbar" aria-label={preview.ratio !== null ? 'Preview frame position' : 'Playback progress'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((preview.ratio !== null ? preview.ratio * 100 : preview.watched))} className="pointer-events-none absolute inset-x-0 bottom-0 z-[3] h-[3px] bg-white/20">
            <span className="block h-full bg-accent transition-[width] duration-150" style={{ width: `${preview.ratio !== null ? preview.ratio * 100 : preview.watched}%` }} />
          </span>
        ) : null}
        <button
          type="button"
          aria-label={item.favorite ? 'Remove from favourites' : 'Add to favourites'}
          aria-pressed={Boolean(item.favorite)}
          disabled={fav.isPending}
          onClick={() => fav.mutate({ mediaId: item.media_id, favorite: !item.favorite })}
          className="absolute right-2 top-2 z-10 grid size-[34px] place-items-center rounded-full bg-black/40 text-white backdrop-blur-sm transition-colors hover:bg-black/60"
        >
          <LikeHeart liked={Boolean(item.favorite)} />
        </button>
      </div>
      <div className={BODY}>
        <div className="h-9 pr-[34px]">
          <h3 className="line-clamp-2 text-sm font-bold leading-[18px] [overflow-wrap:anywhere]">
            <Link to="/watch/$cid/$mid" params={{ cid: item._cid, mid: String(item.id) }} className="outline-none after:absolute after:inset-0 after:z-0 focus-visible:after:ring-2 focus-visible:after:ring-accent">{item.name}</Link>
          </h3>
        </div>
        <div className="mt-2 flex h-[22px] items-center gap-1.5 text-[12.5px] text-fg">
          {people.length ? (
            <>
              <span className="flex">
                {people.slice(0, 2).map((n, i) => (
                  <span key={n} className={cn('grid size-[22px] place-items-center rounded-full border-2 border-surface bg-surface-3 text-[10px] font-semibold text-muted', i > 0 && '-ml-1.5')}>
                    {initials(n)}
                  </span>
                ))}
              </span>
              <span className="truncate">
                {people[0]}
                {people.length > 1 ? <span className="text-muted">+{people.length - 1}</span> : null}
              </span>
            </>
          ) : (
            <span className="text-muted">Unknown performer</span>
          )}
        </div>
        <div className="mt-1.5 flex h-4 items-center gap-[5px] text-xs text-faint">
          {place ? (
            <>
              <Icon name="send" className="size-[13px] shrink-0 text-[#4fb3ea]" />
              <span className="truncate">{place}</span>
            </>
          ) : null}
        </div>
        <div className="mt-2 h-6 overflow-hidden">
        {item.tags.length ? (
          <ul className="flex flex-nowrap gap-x-[5px] overflow-hidden">
            {item.tags.slice(0, 2).map((t, i) => (
              <li key={t} className={cn('max-w-[60%] shrink-0 truncate rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold', TAG_TINTS[i % 2])}>
                {t}
              </li>
            ))}
            {item.tags.length > 2 ? <li className={cn('shrink-0 rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold', TAG_TINTS[2])}>+{item.tags.length - 2}</li> : null}
          </ul>
        ) : null}
        </div>
      </div>
      <CardMenu item={item} className="absolute right-[6px] top-[calc((100cqw-2px)*0.5625+10px)] z-10" />
    </article>
  )
}

/** Same card shell, thumbnail aspect and body metrics as VideoCard (shared constants above). */
export function VideoCardSkeleton({ index = 0 }: { index?: number }) {
  const widths = [['92%', '64%'], ['78%', '48%'], ['88%', '70%'], ['70%', '52%']] as const
  const [a, b] = widths[index % widths.length] ?? widths[0]
  return (
    <div className={CARD} aria-hidden="true">
      <div className={THUMB}>
        <Skeleton className="absolute inset-0 rounded-none" />
      </div>
      <div className={BODY}>
        <div className="h-9 pr-[34px] text-sm font-bold leading-[18px]">
          <SkeletonLine width={a} />
          <SkeletonLine width={b} />
        </div>
        <div className="mt-2 flex h-[22px] items-center gap-1.5">
          <Skeleton className="size-[22px] rounded-full" />
          <Skeleton className="h-3 w-24 rounded" />
        </div>
        <div className="mt-1.5 flex h-4 items-center"><Skeleton className="h-3 w-3/4 rounded" /></div>
        <div className="mt-2 h-6" />
      </div>
    </div>
  )
}
