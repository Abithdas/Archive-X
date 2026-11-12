import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { Entity, EntityKind } from '@/api/types'
import { Icon } from '@/design/Icon'
import { Skeleton } from '@/design/Skeleton'
import { EntityEditor } from './EntityEditor'
import { responsive } from '@/design/responsive'

const imageUrl = (kind: EntityKind, e: Entity) => `/api/${kind}/${e.id}/image?v=${encodeURIComponent(e.image ?? '')}`
const PLACEHOLDER = '/static/icons/ax-placeholder.png'

/** Portrait box shared by the card and its skeleton: 3:4, 16px radius, same as the classic UI. */
/** The poster look used on the related tabs: name and video count over the bottom of the picture. Performers are tall, studios square. */
const POSTER = 'relative w-full overflow-hidden [contain:paint] border bg-[#171a1b] shadow-[0_12px_32px_rgb(0_0_0/0.22)]'
const SHAPE = { performers: 'aspect-[2/3] rounded-[23px] border-white/[0.13]', studios: 'aspect-square rounded-[15px] border-line' } as const

export interface MergePick { keeper: boolean; selected: boolean; onPick: () => void }

export function EntityCard({ kind, entity, merge }: { kind: EntityKind; entity: Entity; merge?: MergePick }) {
  const [broken, setBroken] = useState(false)
  const [editing, setEditing] = useState(false)
  const studio = kind === 'studios'
  const label = entity.video_count === 1 ? '1 video' : `${entity.video_count} videos`
  const img = broken ? { src: PLACEHOLDER } : responsive(imageUrl(kind, entity), '(max-width: 639px) 46vw, 260px')
  const ring = merge?.keeper ? 'border-good ring-2 ring-good/60' : merge?.selected ? 'border-warn ring-2 ring-warn/60' : ''
  return (
    <article aria-label={entity.name} className="group relative">
      {merge ? (
        <button type="button" onClick={merge.onPick} aria-pressed={merge.keeper || merge.selected} aria-label={`${merge.keeper ? 'Keeper: ' : merge.selected ? 'Duplicate: ' : 'Choose '}${entity.name}`} className="absolute inset-0 z-10 rounded-[23px] outline-none focus-visible:ring-2 focus-visible:ring-accent" />
      ) : null}
      <div className={`${POSTER} ${SHAPE[kind]} ${ring}`}>
        {merge && (merge.keeper || merge.selected) ? <span className={`absolute left-2 top-2 z-20 rounded-md px-2 py-0.5 text-[11px] font-bold text-black ${merge.keeper ? 'bg-good' : 'bg-warn'}`}>{merge.keeper ? 'Keep' : 'Merge'}</span> : null}
        {broken ? <img src={PLACEHOLDER} alt="" aria-hidden="true" className="absolute inset-0 h-full w-full scale-125 object-cover opacity-60 blur-xl" /> : null}
        <img
          {...img}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => !broken && setBroken(true)}
          className={broken ? 'absolute inset-0 h-full w-full object-contain p-6' : studio ? 'absolute left-[9%] top-[16.5%] h-[67%] w-[82%] object-contain' : 'absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]'}
        />
        <div className={`absolute inset-x-0 bottom-0 grid gap-[3px] px-[13px] pb-3 pt-12 ${studio ? 'bg-[linear-gradient(rgb(0_0_0/0),rgb(10_11_10/0.52)_25%,rgb(10_11_10/0.88))]' : 'bg-[linear-gradient(rgb(0_0_0/0)_0%,rgb(13_15_17/0.34)_18%,rgb(13_15_17/0.88)_62%,rgb(13_15_17/0.97)_100%)]'}`}>
          <h3 className="truncate text-sm font-bold text-white" title={entity.name}>
            {!merge ? (
              <Link to={kind === 'performers' ? '/performers/$id' : '/studios/$id'} params={{ id: String(entity.id) }} className="outline-none after:absolute after:inset-0 focus-visible:after:ring-2 focus-visible:after:ring-accent">
                {entity.name}
              </Link>
            ) : (
              entity.name
            )}
          </h3>
          <p className="mt-[3px] inline-flex w-fit items-center rounded-full border border-white/[0.16] bg-white/[0.094] px-2 py-[3px] text-[11px] font-semibold leading-[13px] text-white/90">{label}</p>
        </div>
      </div>
      {!merge ? (
        <button type="button" onClick={() => setEditing(true)} title={`Edit ${entity.name}`} aria-label={`Edit ${entity.name}`} className="absolute right-2 top-2 z-20 grid size-9 place-items-center rounded-full bg-black/55 text-white opacity-100 backdrop-blur-sm hover:bg-black/75 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100">
          <Icon name="edit" className="size-4" />
        </button>
      ) : null}
      {editing ? <EntityEditor kind={kind} id={entity.id} onDone={() => setEditing(false)} /> : null}
    </article>
  )
}

/** Same box model as EntityCard: one poster block (the name sits inside it). */
export function EntityCardSkeleton({ kind = 'performers' }: { kind?: EntityKind }) {
  return <Skeleton className={`${POSTER} ${SHAPE[kind]}`} />
}
