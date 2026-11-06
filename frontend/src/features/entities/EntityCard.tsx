import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { Entity, EntityKind } from '@/api/types'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'

const imageUrl = (kind: EntityKind, e: Entity) => `/api/${kind}/${e.id}/image?v=${encodeURIComponent(e.image ?? '')}`

export function EntityCard({ kind, entity }: { kind: EntityKind; entity: Entity }) {
  const [broken, setBroken] = useState(false)
  const label = entity.video_count === 1 ? '1 video' : `${entity.video_count} videos`
  return (
    <article aria-label={entity.name} className="relative overflow-hidden rounded-card border border-line bg-surface transition-shadow focus-within:ring-2 focus-within:ring-accent hover:shadow-lg">
      <div className="relative aspect-[3/4] bg-surface-2">
        {broken ? (
          <div className="grid h-full place-items-center text-4xl font-extrabold text-muted" aria-hidden="true">
            {entity.name.trim().charAt(0).toUpperCase() || '?'}
          </div>
        ) : (
          <img src={imageUrl(kind, entity)} alt="" loading="lazy" decoding="async" onError={() => setBroken(true)} className="h-full w-full object-cover" />
        )}
      </div>
      <div className="p-3 text-sm leading-snug">
        <h3 className="truncate font-bold">
          {kind === 'performers' ? <Link to="/performers/$id" params={{ id: String(entity.id) }} className="outline-none after:absolute after:inset-0">{entity.name}</Link> : entity.name}
        </h3>
        <p className="text-muted">{label}</p>
      </div>
    </article>
  )
}

/** Same box model as EntityCard: portrait block, then a title line and a count line. */
export function EntityCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-card border border-line bg-surface">
      <Skeleton className="aspect-[3/4] w-full rounded-none" />
      <div className="p-3 text-sm leading-snug">
        <SkeletonLine width="70%" />
        <SkeletonLine width="40%" />
      </div>
    </div>
  )
}
