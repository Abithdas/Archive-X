import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { thumbUrl } from '@/api/client'
import { useFavorite } from '@/api/hooks'
import type { LibraryItem } from '@/api/types'
import { Icon } from '@/design/Icon'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { CardMenu } from './CardMenu'
import { formatDuration } from './format'
import { pickKey, useSelection } from './selection'

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
  const duration = formatDuration(item.duration)
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
    <article onClickCapture={pick} data-picked={selecting ? picked : undefined} className={cn(CARD, 'transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-[0_18px_36px_-12px_rgb(0_0_0/0.55)]', selecting && 'cursor-pointer', picked && 'border-accent ring-2 ring-accent/60')} aria-label={item.name}>
      <div className={THUMB}>
        {imgOk ? (
          <img
            src={thumbUrl(item._cid, item.thumbnail_id)}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setImgOk(false)}
            className="absolute inset-0 h-full w-full object-cover"
          />
        ) : null}
        {selecting ? (
          <input type="checkbox" checked={picked} onChange={() => toggle(item._cid, item.id)} aria-label={`Select ${item.name}`} className="absolute left-2 top-2 z-10 size-5 cursor-pointer accent-[var(--color-accent)]" />
        ) : null}
        {item.resolution_category ? <span className={cn(BADGE, 'bottom-2 left-2')}>{item.resolution_category}</span> : null}
        {duration ? <span className={cn(BADGE, 'bottom-2 right-2')}>{duration}</span> : null}
        <button
          type="button"
          aria-label={item.favorite ? 'Remove from favourites' : 'Add to favourites'}
          aria-pressed={Boolean(item.favorite)}
          disabled={fav.isPending}
          onClick={() => fav.mutate({ mediaId: item.media_id, favorite: !item.favorite })}
          className={cn('absolute right-2 top-2 z-10 grid size-[34px] place-items-center rounded-full bg-black/40 backdrop-blur-sm transition-colors hover:bg-black/60', item.favorite ? 'text-warn' : 'text-white')}
        >
          <Icon name="star" className={cn("size-4", Boolean(item.favorite) && "drop-shadow")} />
        </button>
      </div>
      <div className={BODY}>
        <div className="pr-[34px]">
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
        {place ? (
          <div className="mt-1.5 flex items-center gap-[5px] text-xs text-faint">
            <Icon name="send" className="size-[13px] text-[#4fb3ea]" />
            <span className="truncate">{place}</span>
          </div>
        ) : null}
        {item.tags.length ? (
          <ul className="mt-2 flex flex-wrap gap-x-[5px] gap-y-1">
            {item.tags.slice(0, 2).map((t, i) => (
              <li key={t} className={cn('rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold', TAG_TINTS[i % 2])}>
                {t}
              </li>
            ))}
            {item.tags.length > 2 ? <li className={cn('rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold', TAG_TINTS[2])}>+{item.tags.length - 2}</li> : null}
          </ul>
        ) : null}
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
        <div className="pr-[34px] text-sm font-bold leading-[18px]">
          <SkeletonLine width={a} />
          <SkeletonLine width={b} />
        </div>
        <div className="mt-2 flex h-[22px] items-center gap-1.5">
          <Skeleton className="size-[22px] rounded-full" />
          <Skeleton className="h-3 w-24 rounded" />
        </div>
        <Skeleton className="mt-1.5 h-3 w-3/4 rounded" />
      </div>
    </div>
  )
}
