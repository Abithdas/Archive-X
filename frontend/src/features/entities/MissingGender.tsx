import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Button } from '@/design/Button'

/** What to say about performers that have no gender: they are hidden under Female, Male and Trans (they show under All). */
export function missingGenderText(count: number): string {
  return `${count.toLocaleString()} ${count === 1 ? 'performer has' : 'performers have'} no gender, so ${count === 1 ? 'it is' : 'they are'} hidden under Female, Male and Trans. They are still under All.`
}

export function restoredText(r: { restored: number; still_blank: number }): string {
  const done = r.restored ? `Restored ${r.restored.toLocaleString()} from provider data.` : 'Nothing could be restored from provider data.'
  return r.still_blank ? `${done} ${r.still_blank.toLocaleString()} still need a gender: open All, press the edit button on the performer and choose one.` : done
}

export function MissingGender() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const q = useQuery({ queryKey: ['missing-gender'] as const, queryFn: () => api<{ count: number }>('/api/performer-gender/missing'), staleTime: 30_000 })
  const count = typeof q.data?.count === 'number' ? q.data.count : 0
  if (!count) return null
  const restore = async () => {
    setBusy(true)
    try {
      const r = await api<{ restored: number; still_blank: number }>('/api/performer-gender/restore', { method: 'POST' })
      toast(restoredText(r))
      await Promise.all([qc.invalidateQueries({ queryKey: ['entities'] }), qc.invalidateQueries({ queryKey: ['missing-gender'] })])
    } catch (e) { toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not restore the genders.') } finally { setBusy(false) }
  }
  return (
    <div role="status" className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
      <span>{missingGenderText(count)}</span>
      <Button variant="outline" disabled={busy} onClick={() => void restore()}>{busy ? 'Restoring…' : 'Restore from provider data'}</Button>
    </div>
  )
}
