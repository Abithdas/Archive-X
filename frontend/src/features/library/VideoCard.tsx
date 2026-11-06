import { useState } from 'react'
import { thumbUrl } from '@/api/client'
import type { LibraryItem } from '@/api/types'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { formatDuration } from './format'

/** Card metrics live in one place so the skeleton below can never drift from the real card. */
const CARD = 'overflow-hidden rounded-card border border-line bg-surface'
const THUMB = 'relative aspect-video w-full overflow-hidden bg-surface-2'
const BODY = 'grid gap-1.5 p-3 text-[12.5px] leading-[1.45]'

export function VideoCard({ item }: { item: LibraryItem }) {
  const [imgOk, setImgOk] = useState(true)
  const duration = formatDuration(item.duration)
  return (
    <article className={cn(CARD, 'transition-shadow hover:shadow-lg')} aria-label={item.name}>
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
        {item.resolution_category ? (
          <span className="absolute bottom-2 left-2 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-bold text-white">
            {item.resolution_category}
          </span>
        ) : null}
        {duration ? (
          <span className="absolute bottom-2 right-2 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-bold text-white">{duration}</span>
        ) : null}
      </div>
      <div className={BODY}>
        <h3 className="line-clamp-2 font-semibold [overflow-wrap:anywhere]">{item.name}</h3>
        <p className="truncate text-muted">{item.performers.length ? item.performers.join(', ') : 'Unknown performer'}</p>
        <p className="truncate text-muted">{[item.studio, item.channel_title].filter(Boolean).join(' · ')}</p>
        {item.tags.length ? (
          <ul className="flex flex-wrap gap-1.5 pt-0.5">
            {item.tags.slice(0, 3).map((t) => (
              <li key={t} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">
                {t}
              </li>
            ))}
            {item.tags.length > 3 ? <li className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">+{item.tags.length - 3}</li> : null}
          </ul>
        ) : null}
      </div>
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
        <div className="font-semibold">
          <SkeletonLine width={a} />
          <SkeletonLine width={b} />
        </div>
        <SkeletonLine width="55%" />
        <SkeletonLine width="70%" />
      </div>
    </div>
  )
}
