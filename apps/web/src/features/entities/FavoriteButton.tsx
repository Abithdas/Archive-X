import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { cn } from '@/design/cn'
import { favLabel, favText } from './studio'
import { ACTION } from './EntityHero'
import { LikeHeart } from '@/design/LikeHeart'

/** Star toggle for a performer or studio; shows the server's answer, not a guess. */
export function FavoriteButton({ kind, id, initial }: { kind: 'performer' | 'studio'; id: number; initial: boolean }) {
  const qc = useQueryClient()
  const [on, setOn] = useState(initial)
  const [busy, setBusy] = useState(false)
  useEffect(() => setOn(initial), [initial])
  // The heart changes at once; the server's answer settles it, and a failure puts it back.
  const toggle = async () => {
    if (busy) return
    const before = on
    setOn(!before)
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; favorite?: boolean }>(`/api/entities/${kind}/${id}/favorite`, { method: 'POST' })
      setOn(Boolean(r.favorite))
      void qc.invalidateQueries({ queryKey: [kind === 'studio' ? 'studio' : 'performer', id] })
    } catch (e) { setOn(before); toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not change the favourite.') } finally { setBusy(false) }
  }
  return (
    <button type="button" aria-pressed={on} aria-label={favLabel(on)} title={favLabel(on)} onClick={() => void toggle()}
      className={cn(ACTION, on && 'border-[#f40051]/60')}>
      <LikeHeart liked={on} />{favText(on)}
    </button>
  )
}
