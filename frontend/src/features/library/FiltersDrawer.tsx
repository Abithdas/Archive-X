import { useEffect, useState } from 'react'
import { useSuggestions } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { NO_FILTERS, useLibraryStore, type Filters } from './store'

const FIELD = 'h-11 w-full rounded-xl border border-line-2 bg-bg px-3 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'

/** The classic "Filters" panel: studio, performer, tag (with suggestions) and an upload-date range. */
export function FiltersDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const applied = useLibraryStore((s) => s.filters)
  const setFilters = useLibraryStore((s) => s.setFilters)
  const [draft, setDraft] = useState<Filters>(applied)
  const sugg = useSuggestions(open)
  useEffect(() => {
    if (open) setDraft(applied)
  }, [open, applied])
  const set = (k: keyof Filters) => (e: React.ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, [k]: e.target.value })
  const apply = () => {
    setFilters({ ...draft, studio: draft.studio.trim(), performer: draft.performer.trim(), tag: draft.tag.trim() })
    onClose()
  }
  const text = (k: 'studio' | 'performer' | 'tag', label: string, ph: string, list: string[] | undefined) => (
    <label className="grid gap-1.5 text-xs font-semibold text-muted">
      {label}
      <input className={FIELD} list={`sg-${k}`} value={draft[k]} onChange={set(k)} placeholder={ph} autoComplete="off" />
      <datalist id={`sg-${k}`}>{(list ?? []).map((n) => <option key={n} value={n} />)}</datalist>
    </label>
  )
  return (
    <Dialog open={open} onClose={onClose} title="Filters" side="right" actions={<button type="button" onClick={() => setDraft(NO_FILTERS)} className="h-9 rounded-xl border border-line-2 px-3 text-[13px] font-semibold">Reset</button>}>
      <form
        className="grid gap-3.5"
        onSubmit={(e) => {
          e.preventDefault()
          apply()
        }}
      >
        {text('studio', 'Studio', 'Any studio…', sugg.data?.studios)}
        {text('performer', 'Performer', 'Any performer…', sugg.data?.performers)}
        {text('tag', 'Tag', 'Any tag…', sugg.data?.tags)}
        <div className="grid gap-1.5 text-xs font-semibold text-muted">
          Upload date
          <div className="grid grid-cols-2 gap-2">
            <input type="date" aria-label="From date" className={FIELD} value={draft.dateFrom} onChange={set('dateFrom')} max={draft.dateTo || undefined} />
            <input type="date" aria-label="To date" className={FIELD} value={draft.dateTo} onChange={set('dateTo')} min={draft.dateFrom || undefined} />
          </div>
        </div>
        <button type="submit" className="h-12 rounded-xl bg-accent text-[15px] font-bold text-accent-fg hover:brightness-105">Apply filters</button>
      </form>
    </Dialog>
  )
}
