import { useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { cn } from '@/design/cn'
import { Segmented } from '@/design/Segmented'
import { modeLabel, withPatch, type ChannelMark, type Mode, type Patch } from './content'
import { SaveBar } from './SaveBar'
import { errorText, useChannelMarks, useSaveMarks } from './useContentMode'

const HOLDS = [{ value: 'both', label: 'Videos + photos' }, { value: 'videos', label: 'Videos' }, { value: 'photos', label: 'Photos' }] as const
const FILTERS = [{ value: 'all', label: 'All channels' }, { value: 'in', label: 'In the library' }, { value: 'out', label: 'Not in the library' }, { value: 'sfw', label: 'Marked SFW' }, { value: 'nsfw', label: 'Marked NSFW' }] as const
type Filter = (typeof FILTERS)[number]['value']
const BULK = 'h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold transition-transform hover:bg-fg/5 active:scale-95'
const saved = (c: ChannelMark): Patch => ({ in_library: Boolean(c.in_library), content_rating: c.content_rating, holds: c.holds })

/** Settings, Telegram, Channels: for each channel, whether it is in the library, SFW or NSFW, and what it holds. Changes are kept aside until Save. */
export function ChannelMarks() {
  const q = useChannelMarks()
  const save = useSaveMarks()
  const [draft, setDraft] = useState<Record<string, Patch>>({})
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [find, setFind] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [showUnnamed, setShowUnnamed] = useState(false)
  const last = useRef<string | null>(null)
  const all = useMemo(() => (q.data?.channels ?? []).filter((c) => c.kind !== 'local'), [q.data])
  const unnamed = all.filter((c) => c.unnamed).length
  const rows = useMemo(() => {
    const text = find.trim().toLowerCase()
    return all.filter((c) => {
      if (c.unnamed && !showUnnamed && !text) return false
      const v = { ...saved(c), ...draft[c.telegram_id] }
      if (filter === 'in' && !v.in_library) return false
      if (filter === 'out' && v.in_library) return false
      if (filter === 'sfw' && v.content_rating !== 'sfw') return false
      if (filter === 'nsfw' && v.content_rating !== 'nsfw') return false
      return !text || c.title.toLowerCase().includes(text)
    })
  }, [all, draft, find, filter, showUnnamed])
  const ids = [...picked]
  const changes = Object.keys(draft).length
  const edit = (id: string, patch: Patch) => {
    const c = all.find((x) => x.telegram_id === id)
    if (c) setDraft((d) => withPatch(d, id, saved(c), patch))
  }
  const editMany = (list: string[], patch: Patch) => list.forEach((id) => edit(id, patch))
  const toggle = (id: string, shift: boolean) => {
    const anchor = last.current   // read now: the update below runs later, after the anchor has moved on
    setPicked((s) => {
      const n = new Set(s)
      const from = rows.findIndex((r) => r.telegram_id === anchor)
      const to = rows.findIndex((r) => r.telegram_id === id)
      if (shift && from >= 0 && to >= 0) rows.slice(Math.min(from, to), Math.max(from, to) + 1).forEach((r) => n.add(r.telegram_id))   // shift-click picks a whole run
      else if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
    last.current = id
  }
  const allOn = rows.length > 0 && rows.every((r) => picked.has(r.telegram_id))
  const commit = () => save.mutate(draft, {
    onSuccess: (n) => { setDraft({}); setPicked(new Set()); toast.success(`Saved ${n.toLocaleString()} channel${n === 1 ? '' : 's'}.`) },
    onError: (e) => toast.error(errorText(e, 'Could not save. Your changes are still here.')),
  })

  return (
    <section aria-label="Channels" className="rounded-[18px] border border-line bg-surface p-5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-xl font-extrabold">Channels</h3>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Show channels" value={filter} onChange={(e) => setFilter(e.target.value as Filter)} className="h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm">{FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}</select>
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a channel" aria-label="Find a channel" className="h-10 w-48 rounded-xl border border-line-2 bg-bg px-3 text-sm" />
        </div>
      </div>
      <p className="mb-3 text-sm text-muted">Only channels in the library are shown in the app, and only those marked like the current mode ({modeLabel(q.data?.mode ?? 'sfw')}). A channel is either SFW or NSFW, never both. Choose as many as you like, then press Save.</p>
      {q.data?.hidden ? <p className="mb-3 rounded-xl bg-surface-2 px-3 py-2 text-xs text-muted">{q.data.hidden.toLocaleString()} channel{q.data.hidden === 1 ? ' is' : 's are'} in the library as NSFW and not listed in SFW mode. Switch to NSFW to manage {q.data.hidden === 1 ? 'it' : 'them'}.</p> : null}
      {q.isPending ? <p role="status" className="py-3 text-sm text-muted">Loading channels…</p> : q.isError ? <p role="alert" className="py-3 text-sm text-warn">Could not load the channels. Connect Telegram first, then refresh.</p> : !all.length ? <p className="py-3 text-sm text-muted">No channels to sort here. Connect Telegram to list them.</p> : (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={allOn} onChange={() => setPicked(allOn ? new Set() : new Set(rows.map((r) => r.telegram_id)))} aria-label="Select all channels" />Select all shown</label>
            <span className="text-muted" aria-live="polite">{ids.length ? `${ids.length} selected:` : 'Tick channels (shift-click for a run), then choose what they become'}</span>
            {ids.length ? (
              <>
                <button type="button" onClick={() => editMany(ids, { in_library: true })} className={BULK}>Add to library</button>
                <button type="button" onClick={() => editMany(ids, { in_library: false })} className={BULK}>Remove from library</button>
                <button type="button" onClick={() => editMany(ids, { content_rating: 'sfw' })} className={BULK}>Mark SFW</button>
                <button type="button" onClick={() => editMany(ids, { content_rating: 'nsfw' })} className={BULK}>Mark NSFW</button>
              </>
            ) : null}
            {unnamed ? <button type="button" onClick={() => setShowUnnamed((v) => !v)} aria-pressed={showUnnamed} className={cn(BULK, 'ml-auto')}>{showUnnamed ? 'Hide' : 'Show'} {unnamed.toLocaleString()} unnamed</button> : null}
          </div>
          <ul aria-label="Channel list" className="grid max-h-[32rem] divide-y divide-line overflow-y-auto rounded-xl border border-line">
            {rows.length ? rows.map((c, i) => <Row key={c.telegram_id} i={i} c={c} v={{ ...saved(c), ...draft[c.telegram_id] }} edited={Boolean(draft[c.telegram_id])} picked={picked.has(c.telegram_id)} onPick={(shift) => toggle(c.telegram_id, shift)} edit={(p) => edit(c.telegram_id, p)} />) : <li className="p-4 text-sm text-muted">No channels match.</li>}
          </ul>
          <SaveBar count={changes} busy={save.isPending} onSave={commit} onDiscard={() => setDraft({})} />
        </>
      )}
    </section>
  )
}

function Row({ c, i, v, edited, picked, onPick, edit }: { c: ChannelMark; i: number; v: Patch; edited: boolean; picked: boolean; onPick: (shift: boolean) => void; edit: (p: Patch) => void }) {
  return (
    <li style={{ '--i': i } as React.CSSProperties} className={cn('ax-rise flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 transition-[background-color,opacity] duration-200', !v.in_library && 'opacity-70', edited && 'bg-accent/10', picked && !edited && 'bg-fg/5')}>
      <input type="checkbox" checked={picked} onClick={(e) => onPick(e.shiftKey)} onChange={() => undefined} aria-label={`Select ${c.title}`} />
      <div className="min-w-0 flex-1 basis-48"><b className="block truncate text-sm">{c.title}</b><small className="text-xs text-muted">{c.videos.toLocaleString()} videos · {c.photos.toLocaleString()} photos{edited ? ' · changed' : ''}</small></div>
      <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(v.in_library)} onChange={(e) => edit({ in_library: e.target.checked })} aria-label={`${c.title} is in the library`} />In library</label>
      <Segmented<Mode> label={`${c.title} content`} value={v.content_rating ?? 'nsfw'} onChange={(m) => edit({ content_rating: m })} options={[{ value: 'sfw', label: 'SFW' }, { value: 'nsfw', label: 'NSFW' }]} />
      <select aria-label={`${c.title} holds`} value={v.holds} onChange={(e) => edit({ holds: e.target.value })} className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm">
        {HOLDS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
      </select>
    </li>
  )
}
