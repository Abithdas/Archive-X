import { useState } from 'react'
import { cn } from '@/design/cn'
import { BlurImg } from '@/design/BlurImg'
import { HScroll } from '@/design/HScroll'
import { bestGuess, groupLabel, isAssigned, peopleRow, viewFilter, type FaceGroup } from './faces'
import { usePhotoStore } from './store'
import { newMatchesText, type ReviewCount } from './useFaceReview'

function Face({ g, active, fresh, onPick }: { g: FaceGroup; active: boolean; fresh?: ReviewCount; onPick: () => void }) {
  const [ok, setOk] = useState(true)
  const named = isAssigned(g)
  const guess = bestGuess(g)
  return (
    <button type="button" onClick={onPick} aria-pressed={active} title={named ? `${groupLabel(g)}${fresh ? ` · ${newMatchesText(fresh.suggestions)}` : ''}` : guess ? `Is this ${guess.name}?` : 'Who is this?'}
      className="group grid w-[76px] shrink-0 content-start justify-items-center gap-1 text-center text-xs">
      <span className="relative block size-16">
        <span className={cn('grid size-16 place-items-center overflow-hidden rounded-full bg-surface-2 ring-2 ring-offset-2 ring-offset-bg transition', active ? 'ring-accent' : named ? 'ring-transparent group-hover:ring-accent/50' : 'ring-warn/50 group-hover:ring-warn')}>
          {ok && g.representative_url ? <BlurImg src={g.representative_url} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="size-full object-cover" /> : <span aria-hidden="true" className="text-lg font-bold text-muted">?</span>}
        </span>
        {named && fresh ? <span role="img" aria-label={newMatchesText(fresh.suggestions)} data-testid="new-matches" className="absolute -right-1.5 -top-1.5 z-10 grid h-5 min-w-5 place-items-center rounded-full bg-accent px-1 text-[11px] font-bold leading-none text-accent-fg ring-2 ring-bg">{fresh.suggestions > 99 ? '99+' : fresh.suggestions}</span> : null}
      </span>
      <span className={cn('line-clamp-2 w-full leading-[14px]', named ? 'font-semibold text-fg' : 'text-muted')}>{named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Add name'}</span>
      <span className="text-[11px] font-semibold tabular-nums text-muted">{Number(g.photo_count ?? 0).toLocaleString()} photos</span>
    </button>
  )
}

/** People across the top of the photo library. Click a person to see their photos; a person without a name opens Image Analysis to confirm who it is. */
export function PeopleRow({ groups, onManage, fresh }: { groups: readonly FaceGroup[]; onManage: () => void; fresh?: Record<number, ReviewCount> }) {
  const { face, setFace } = usePhotoStore()
  const row = peopleRow(groups)
  if (!row.length) return null
  return (
    <section aria-label="People" className="mb-4">
      <div className="mb-2 flex items-center justify-between"><h2 className="text-lg font-bold">People</h2>
        <button type="button" onClick={onManage} className="text-xs font-semibold text-accent hover:underline">Manage people</button></div>
      <HScroll label="People, scroll sideways" className="flex gap-3 pb-2 pt-2" arrows={false}>
        {row.map((g) => {
          const active = face?.groupId === Number(g.id)
          return <Face key={g.id} g={g} active={active} fresh={g.performer_id ? fresh?.[Number(g.performer_id)] : undefined} onPick={() => (active ? setFace(null) : isAssigned(g) || Number(g.photo_count ?? 0) > 0 ? setFace(viewFilter(g).filter) : onManage())} />
        })}
      </HScroll>
    </section>
  )
}
