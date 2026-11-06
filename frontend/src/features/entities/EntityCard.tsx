import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { Entity, EntityKind } from '@/api/types'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'

const imageUrl = (kind: EntityKind, e: Entity) => `/api/${kind}/${e.id}/image?v=${encodeURIComponent(e.image ?? '')}`
const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/** Portrait box shared by the card and its skeleton: 3:4, 16px radius, same as the classic UI. */
const PORTRAIT = 'relative aspect-[3/4] w-full overflow-hidden rounded-card border border-transparent bg-surface-3'

export function EntityCard({ kind, entity }: { kind: EntityKind; entity: Entity }) {
  const [broken, setBroken] = useState(false)
  const label = entity.video_count === 1 ? '1 video' : `${entity.video_count} videos`
  const src = broken ? PLACEHOLDER : imageUrl(kind, entity)
  return (
    <article aria-label={entity.name} className="group relative grid gap-2.5 text-center">
      <div className={PORTRAIT}>
        {broken ? <img src={PLACEHOLDER} alt="" aria-hidden="true" className="absolute inset-0 h-full w-full scale-125 object-cover opacity-60 blur-xl" /> : null}
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => !broken && setBroken(true)}
          className={broken ? 'absolute inset-0 h-full w-full object-contain p-6' : 'absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]'}
        />
      </div>
      <div className="px-1 leading-snug">
        <h3 className="truncate text-sm font-semibold">
          {kind === 'performers' ? (
            <Link to="/performers/$id" params={{ id: String(entity.id) }} className="outline-none after:absolute after:inset-0 after:rounded-card focus-visible:after:ring-2 focus-visible:after:ring-accent">
              {entity.name}
            </Link>
          ) : (
            entity.name
          )}
        </h3>
        <p className="text-xs text-muted">{label}</p>
      </div>
    </article>
  )
}

/** Same box model as EntityCard: portrait block, then a name line and a count line. */
export function EntityCardSkeleton() {
  return (
    <div className="grid gap-2.5">
      <Skeleton className={`${PORTRAIT} rounded-card`} />
      <div className="px-1 text-sm leading-snug">
        <SkeletonLine width="60%" className="mx-auto" />
        <div className="text-xs"><SkeletonLine width="35%" className="mx-auto" /></div>
      </div>
    </div>
  )
}
