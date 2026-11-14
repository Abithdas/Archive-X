import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Button } from '@/design/Button'
import type { EntityKind } from '@/api/types'

interface Missing { performers: number; studios: number; running?: boolean }

export function missingPicturesText(count: number, kind: EntityKind, running: boolean): string {
  const noun = kind === 'performers' ? (count === 1 ? 'performer has' : 'performers have') : (count === 1 ? 'studio has' : 'studios have')
  return running ? `Fetching pictures… ${count.toLocaleString()} still to do.` : `${count.toLocaleString()} ${noun} no picture yet. They are fetched in the background every few hours.`
}

/** Performers or studios without a picture: how many, and a button to fetch them now. */
export function MissingPictures({ kind }: { kind: EntityKind }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const q = useQuery({
    queryKey: ['missing-pictures'] as const,
    queryFn: () => api<Missing>('/api/entity-images/missing'),
    staleTime: 30_000,
    refetchInterval: (query) => (query.state.data?.running ? 5000 : false),
  })
  const count = typeof q.data?.[kind] === 'number' ? q.data[kind] : 0
  if (!count) return null
  const running = Boolean(q.data?.running)
  const start = async () => {
    setBusy(true)
    try {
      await api('/api/entity-images/fetch-missing', { method: 'POST' })
      toast('Fetching the missing pictures…')
      await qc.invalidateQueries({ queryKey: ['missing-pictures'] })
      setTimeout(() => void qc.invalidateQueries({ queryKey: ['entities'] }), 15_000)
    } catch (e) { toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not start fetching.') } finally { setBusy(false) }
  }
  return (
    <div role="status" className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2 text-sm">
      <span>{missingPicturesText(count, kind, running)}</span>
      <Button variant="outline" disabled={busy || running} onClick={() => void start()}>{running ? 'Fetching…' : 'Fetch now'}</Button>
    </div>
  )
}
