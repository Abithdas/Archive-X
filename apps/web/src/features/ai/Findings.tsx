import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/api/client'
import { cn } from '@/design/cn'
import { FILTERS, INTRO, KIND, actions, knowledgeLine, type FindingStatus, type FindingsState } from './findingsLogic'

const BTN = 'inline-flex h-9 items-center rounded-lg border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'

/** The notebook tab: its notes in four states with the buttons to move a note between them, and what the assistant has read about Archive X. */
export function Findings() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<FindingStatus>('OPEN')
  const q = useQuery({ queryKey: ['ai-findings', filter] as const, queryFn: () => api<FindingsState>(`/api/ai/findings?status=${filter}`), retry: false })
  const refresh = () => void qc.invalidateQueries({ queryKey: ['ai-findings'] })
  const decide = useMutation({ mutationFn: (v: { id: number; to: FindingStatus }) => api(`/api/ai/findings/${v.id}`, { method: 'POST', json: { status: v.to } }), onSuccess: refresh })
  const rebuild = useMutation({ mutationFn: () => api('/api/ai/knowledge/rebuild', { method: 'POST', json: {} }), onSuccess: refresh })
  const counts = q.data?.counts
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <h2 className="text-lg font-bold">Findings</h2>
      <p className="mb-3 text-xs text-muted">{INTRO}</p>
      <div role="tablist" aria-label="Findings" className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button key={f.value} type="button" role="tab" aria-selected={filter === f.value} onClick={() => setFilter(f.value)} className={cn('rounded-full border px-3 py-1 text-sm font-semibold', filter === f.value ? 'border-accent bg-accent/10 text-fg' : 'border-line text-muted hover:bg-fg/5')}>
            {f.label}{counts ? ` · ${counts[f.value]}` : ''}
          </button>
        ))}
      </div>
      {q.isPending ? <p role="status" className="py-6 text-center text-sm text-muted">Loading…</p> : q.isError ? <p role="alert" className="py-6 text-center text-sm text-warn">The notebook could not be loaded.</p> : q.data.findings.length ? (
        <ul className="grid gap-2">
          {q.data.findings.map((f) => (
            <li key={f.id} className="rounded-xl border border-line p-3 text-left">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[11px] font-bold text-muted">{KIND[f.kind] ?? 'Note'}</span>
                <b className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">{f.title}</b>
              </div>
              {f.detail ? <p className="mt-1.5 whitespace-pre-wrap text-sm text-muted [overflow-wrap:anywhere]">{f.detail}</p> : null}
              <div className="mt-2 flex flex-wrap gap-2">
                {actions(f.status).map((a) => <button key={a.to} type="button" disabled={decide.isPending} onClick={() => decide.mutate({ id: f.id, to: a.to })} aria-label={`${a.label}: ${f.title}`} className={BTN}>{a.label}</button>)}
              </div>
            </li>
          ))}
        </ul>
      ) : <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">{filter === 'OPEN' ? 'Nothing to review. Ask the assistant to look for problems, and what it finds is written here.' : 'Nothing here yet.'}</p>}
      {decide.isError ? <p role="alert" className="mt-2 text-sm text-warn">Could not save that. Try again.</p> : null}
      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-3">
        <span className="min-w-0 flex-1 text-xs text-muted" role="status">{knowledgeLine(q.data?.knowledge)}</span>
        <button type="button" className={BTN} disabled={rebuild.isPending} onClick={() => rebuild.mutate()}>{rebuild.isPending ? 'Reading…' : 'Read it again'}</button>
      </div>
    </div>
  )
}
