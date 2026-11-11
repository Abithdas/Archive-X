import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { favLabel, favText } from './studio'

/** Star toggle for a performer or studio; shows the server's answer, not a guess. */
export function FavoriteButton({ kind, id, initial }: { kind: 'performer' | 'studio'; id: number; initial: boolean }) {
  const qc = useQueryClient()
  const [on, setOn] = useState(initial)
  const [busy, setBusy] = useState(false)
  useEffect(() => setOn(initial), [initial])
  const toggle = async () => {
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; favorite?: boolean }>(`/api/entities/${kind}/${id}/favorite`, { method: 'POST' })
      setOn(Boolean(r.favorite))
      void qc.invalidateQueries({ queryKey: [kind === 'studio' ? 'studio' : 'performer', id] })
    } catch (e) { toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not change the favourite.') } finally { setBusy(false) }
  }
  return (
    <button type="button" disabled={busy} aria-pressed={on} aria-label={favLabel(on)} title={favLabel(on)} onClick={() => void toggle()}
      className={cn('inline-flex h-10 items-center gap-2 rounded-xl border px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60', on ? 'border-warn text-warn' : 'border-line-2')}>
      <Icon name="star" className={cn('size-4', on && 'drop-shadow')} />{favText(on)}
    </button>
  )
}
