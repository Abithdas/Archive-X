import { useState } from 'react'
import { cn } from '@/design/cn'
import { BlurImg } from '@/design/BlurImg'
import { HScroll } from '@/design/HScroll'
import { bestGuess, groupLabel, isAssigned, peopleRow, viewFilter, type FaceGroup } from './faces'
import { usePhotoStore } from './store'

function Face({ g, active, onPick }: { g: FaceGroup; active: boolean; onPick: () => void }) {
  const [ok, setOk] = useState(true)
  const named = isAssigned(g)
  const guess = bestGuess(g)
  return (
    <button type="button" onClick={onPick} aria-pressed={active} title={named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Who is this?'}
      className="group grid w-[76px] shrink-0 content-start justify-items-center gap-1 text-center text-xs">
      <span className={cn('relative grid size-16 place-items-center overflow-hidden rounded-full bg-surface-2 ring-2 ring-offset-2 ring-offset-bg transition', active ? 'ring-accent' : named ? 'ring-transparent group-hover:ring-accent/50' : 'ring-warn/50 group-hover:ring-warn')}>
        {ok && g.representative_url ? <BlurImg src={g.representative_url} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="size-full object-cover" /> : <span aria-hidden="true" className="text-lg font-bold text-muted">?</span>}
      </span>
      <span className={cn('line-clamp-2 w-full leading-[14px]', named ? 'font-semibold text-fg' : 'text-muted')}>{named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Add name'}</span>
      <span className="text-[10.5px] text-faint">{Number(g.photo_count ?? 0).toLocaleString()} photos</span>
    </button>
  )
}

/** People across the top of the photo library. Click a person to see their photos; a person without a name opens Image Analysis to confirm who it is. */
export function PeopleRow({ groups, onManage }: { groups: readonly FaceGroup[]; onManage: () => void }) {
  const { face, setFace } = usePhotoStore()
  const row = peopleRow(groups)
  if (!row.length) return null
  return (
    <section aria-label="People" className="mb-4">
      <div className="mb-2 flex items-center justify-between"><h2 className="text-lg font-bold">People</h2>
        <button type="button" onClick={onManage} className="text-xs font-semibold text-accent hover:underline">Manage people</button></div>
      <HScroll label="People, scroll sideways" className="flex gap-3 pb-2 pt-1">
        {row.map((g) => {
          const active = face?.groupId === Number(g.id)
          return <Face key={g.id} g={g} active={active} onPick={() => (active ? setFace(null) : isAssigned(g) || Number(g.photo_count ?? 0) > 0 ? setFace(viewFilter(g).filter) : onManage())} />
        })}
      </HScroll>
    </section>
  )
}
