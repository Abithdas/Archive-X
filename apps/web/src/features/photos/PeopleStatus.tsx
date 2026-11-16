import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { detailLine, problemOf, type FaceLearning, type NameList } from './peopleStatus'

/** Under the People heading: a headline, one small line of detail, and a warning only when a name list could not be read. */
export function PeopleStatus({ headline }: { headline: string }) {
  const qc = useQueryClient()
  const [asking, setAsking] = useState(false)
  const names = useQuery({
    queryKey: ['name-list'] as const,
    queryFn: () => api<NameList>('/api/photos/faces/performers/name-list'),
    refetchInterval: (q) => (Object.values(q.state.data?.providers ?? {}).some((p) => p.status === 'running') ? 5000 : 60000),
    retry: false,
  })
  const learning = useQuery({ queryKey: ['face-learning'] as const, queryFn: () => api<FaceLearning>('/api/photos/faces/learning/status'), retry: false, staleTime: 30_000 })
  const detail = detailLine(names.data, learning.data)
  const problem = problemOf(names.data)
  const retry = async () => {
    setAsking(true)
    try { await api('/api/photos/faces/performers/name-list/sync', { method: 'POST' }); toast('Reading the performer names again…'); await qc.invalidateQueries({ queryKey: ['name-list'] }) }
    catch (e) { toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not start that.') } finally { setAsking(false) }
  }
  return (
    <div className="grid min-w-0 gap-1">
      <p role="status" aria-live="polite" className="text-sm font-semibold [overflow-wrap:anywhere]">{headline}</p>
      {detail ? <p className="text-xs text-muted [overflow-wrap:anywhere]">{detail}</p> : null}
      {problem ? (
        <p role="alert" className="text-xs text-warn [overflow-wrap:anywhere]">
          <Icon name="alert" className="mr-1.5 inline size-3.5 -translate-y-px" />
          {problem.provider} names are not updating: {problem.reason}.{' '}
          <button type="button" disabled={asking} onClick={() => void retry()} className="font-semibold text-accent hover:underline disabled:opacity-60">Try again</button>
        </p>
      ) : null}
    </div>
  )
}
