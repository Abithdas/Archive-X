import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { Entity, EntityKind } from '@/api/types'
import { Icon } from '@/design/Icon'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'
import { EntityEditor } from './EntityEditor'
import { responsive } from '@/design/responsive'

const imageUrl = (kind: EntityKind, e: Entity) => `/api/${kind}/${e.id}/image?v=${encodeURIComponent(e.image ?? '')}`
const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/** Portrait box shared by the card and its skeleton: 3:4, 16px radius, same as the classic UI. */
const PORTRAIT = 'relative aspect-[3/4] w-full overflow-hidden [contain:paint] rounded-card border border-transparent bg-surface-3'

export interface MergePick { keeper: boolean; selected: boolean; onPick: () => void }

export function EntityCard({ kind, entity, merge }: { kind: EntityKind; entity: Entity; merge?: MergePick }) {
  const [broken, setBroken] = useState(false)
  const [editing, setEditing] = useState(false)
  const label = entity.video_count === 1 ? '1 video' : `${entity.video_count} videos`
  const img = broken ? { src: PLACEHOLDER } : responsive(imageUrl(kind, entity), '(max-width: 639px) 46vw, 260px')
  return (
    <article aria-label={entity.name} className="group relative grid gap-2.5 text-center">
      {merge ? (
        <button type="button" onClick={merge.onPick} aria-pressed={merge.keeper || merge.selected} aria-label={`${merge.keeper ? 'Keeper: ' : merge.selected ? 'Duplicate: ' : 'Choose '}${entity.name}`} className="absolute inset-0 z-10 rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent" />
      ) : null}
      <div className={`${PORTRAIT} ${merge?.keeper ? 'border-good ring-2 ring-good/60' : merge?.selected ? 'border-warn ring-2 ring-warn/60' : ''}`}>
        {merge && (merge.keeper || merge.selected) ? <span className={`absolute left-2 top-2 z-20 rounded-md px-2 py-0.5 text-[11px] font-bold text-black ${merge.keeper ? 'bg-good' : 'bg-warn'}`}>{merge.keeper ? 'Keep' : 'Merge'}</span> : null}
        {broken ? <img src={PLACEHOLDER} alt="" aria-hidden="true" className="absolute inset-0 h-full w-full scale-125 object-cover opacity-60 blur-xl" /> : null}
        <img
          {...img}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => !broken && setBroken(true)}
          className={broken ? 'absolute inset-0 h-full w-full object-contain p-6' : 'absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]'}
        />
      </div>
      {!merge ? (
        <button type="button" onClick={() => setEditing(true)} title={`Edit ${entity.name}`} aria-label={`Edit ${entity.name}`} className="absolute right-2 top-2 z-20 grid size-9 place-items-center rounded-full bg-black/55 text-white opacity-100 backdrop-blur-sm hover:bg-black/75 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100">
          <Icon name="edit" className="size-4" />
        </button>
      ) : null}
      {editing ? <EntityEditor kind={kind} id={entity.id} onDone={() => setEditing(false)} /> : null}
      <div className="px-1 leading-snug">
        <h3 className="truncate text-sm font-semibold">
          {!merge ? (
            <Link to={kind === 'performers' ? '/performers/$id' : '/studios/$id'} params={{ id: String(entity.id) }} className="outline-none after:absolute after:inset-0 after:rounded-card focus-visible:after:ring-2 focus-visible:after:ring-accent">
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

/** Studio directory tile: a small logo beside the name and video count (the classic studio list). */
export function StudioTile({ entity }: { entity: Entity }) {
  const [broken, setBroken] = useState(false)
  const label = entity.video_count === 1 ? '1 video' : `${entity.video_count} videos`
  const src = `/api/studios/${entity.id}/image?v=${encodeURIComponent(entity.image ?? '')}`
  return (
    <article aria-label={entity.name} className="group relative flex min-h-[92px] items-center gap-3 overflow-hidden rounded-2xl border border-line bg-surface p-3.5 transition-colors hover:bg-surface-2">
      <span className="grid size-[69px] shrink-0 place-items-center overflow-hidden rounded-xl bg-bg/60">
        <img {...(broken ? { src: PLACEHOLDER } : responsive(src, '69px', [160, 240]))} alt="" loading="lazy" decoding="async" onError={() => !broken && setBroken(true)} className="size-full object-contain" />
      </span>
      <div className="min-w-0 px-0.5 py-2 leading-snug">
        <h3 className="truncate text-[15px] font-bold">
          <Link to="/studios/$id" params={{ id: String(entity.id) }} className="outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-accent">{entity.name}</Link>
        </h3>
        <p className="text-[13px] text-muted">{label}</p>
      </div>
    </article>
  )
}

export function StudioTileSkeleton() {
  return (
    <div className="flex min-h-[92px] items-center gap-3 rounded-2xl border border-line bg-surface p-3.5">
      <Skeleton className="size-[69px] shrink-0 rounded-xl" />
      <div className="min-w-0 flex-1 text-[15px]"><SkeletonLine width="70%" /><div className="text-[13px]"><SkeletonLine width="35%" /></div></div>
    </div>
  )
}
