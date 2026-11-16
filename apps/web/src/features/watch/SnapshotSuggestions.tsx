import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/api/client'
import type { LibraryItem } from '@/api/types'
import { cn } from '@/design/cn'
import type { Person, Studio } from './quickedit'
import { openSuggestions, reasonOf, runNote, suggestionList, type SnapshotRun, type SnapshotSuggestion } from './snapshotSuggestions'

/** "From snapshots": performers and a studio proposed from the video's own 36 snapshots. Add puts them in the dialog (Save applies them). */
export function SnapshotSuggestions({ item, performers, studio, onAddPerformer, onSetStudio }: { item: LibraryItem; performers: Person[]; studio: Studio | null; onAddPerformer: (p: Person) => void; onSetStudio: (s: Studio) => void }) {
  const qc = useQueryClient()
  const key = ['snapshot-suggestions', item._cid, item.id]
  const [note, setNote] = useState('')
  const query = useQuery({ queryKey: key, queryFn: async () => suggestionList(await api(`/api/media/snapshot-suggestions?channel_id=${encodeURIComponent(String(item._cid))}&message_id=${item.id}`).catch(() => null)), staleTime: 30_000 })
  const check = useMutation({
    mutationFn: () => api<SnapshotRun>('/api/media/snapshot-suggestions/run', { method: 'POST', json: { channel_id: item._cid, message_id: item.id } }),
    onSuccess: (r) => { qc.setQueryData(key, suggestionList(r)); setNote(runNote(r)) },
    onError: () => setNote('Could not look at the snapshots. Try again.'),
  })
  const reject = useMutation({
    mutationFn: (s: SnapshotSuggestion) => api('/api/media/snapshot-suggestions/reject', { method: 'POST', json: { channel_id: item._cid, message_id: item.id, kind: s.kind, id: s.id } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
  })
  const open = openSuggestions(query.data ?? [], performers, studio)
  const add = (s: SnapshotSuggestion) => (s.kind === 'performer' ? onAddPerformer({ name: s.name, id: s.id, kind: 'local' }) : onSetStudio({ name: s.name, id: s.id, kind: 'local' }))
  return (
    <section className="grid min-w-0 content-start gap-2 rounded-2xl border border-line p-3" aria-label="From snapshots">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold">From snapshots</h3>
        <button type="button" disabled={check.isPending} onClick={() => check.mutate()} className="ml-auto h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">{check.isPending ? 'Looking…' : 'Check snapshots'}</button>
      </div>
      {open.length ? (
        <ul className="grid gap-1.5">
          {open.map((s) => (
            <li key={`${s.kind}:${s.id}`} className="flex flex-wrap items-center gap-2 rounded-xl border border-line px-3 py-2">
              <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-bold', s.kind === 'studio' ? 'bg-surface-3 text-muted' : 'bg-accent/15 text-accent')}>{s.kind === 'studio' ? 'Studio' : 'Performer'}</span>
              <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{s.name}</span><span className="block truncate text-[11px] text-muted">{reasonOf(s)}</span></span>
              <button type="button" onClick={() => add(s)} aria-label={`Add ${s.name}`} className="h-8 rounded-lg bg-accent px-3 text-sm font-bold text-accent-fg">Add</button>
              <button type="button" onClick={() => reject.mutate(s)} aria-label={`${s.name} is not them`} className="h-8 rounded-lg border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5">Not them</button>
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-muted">No suggestions yet. The snapshots can show a studio watermark or a performer’s face.</p>}
      {note ? <p role="status" className="text-[11px] text-muted">{note}</p> : null}
    </section>
  )
}
