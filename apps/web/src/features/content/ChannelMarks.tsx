import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { cn } from '@/design/cn'
import { Segmented } from '@/design/Segmented'
import { modeLabel, type ChannelMark, type Mode } from './content'
import { errorText, useChannelMarks, useSetMarks } from './useContentMode'

const HOLDS = [{ value: 'both', label: 'Videos + photos' }, { value: 'videos', label: 'Videos' }, { value: 'photos', label: 'Photos' }] as const

/** Settings, Telegram, Channels: for each channel, whether it is in the library, SFW or NSFW, and what it holds. A channel is wholly one kind. */
export function ChannelMarks() {
  const q = useChannelMarks()
  const set = useSetMarks()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [find, setFind] = useState('')
  const rows = useMemo(() => (q.data?.channels ?? []).filter((c) => c.kind !== 'local').filter((c) => !find.trim() || c.title.toLowerCase().includes(find.trim().toLowerCase())), [q.data, find])
  const ids = [...picked]
  const apply = (ids: string[], body: { in_library?: boolean; content_rating?: Mode; holds?: string }) =>
    set.mutate({ ids, ...body }, { onError: (e) => toast.error(errorText(e, 'Could not save the marks.')) })
  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const allOn = rows.length > 0 && rows.every((r) => picked.has(r.telegram_id))

  return (
    <section aria-label="Channels" className="rounded-[18px] border border-line bg-surface p-5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-xl font-extrabold">Channels</h3>
        <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a channel" aria-label="Find a channel" className="h-10 w-56 rounded-xl border border-line-2 bg-bg px-3 text-sm" />
      </div>
      <p className="mb-3 text-sm text-muted">Only channels in the library are shown in the app, and only those marked like the current mode ({modeLabel(q.data?.mode ?? 'sfw')}). A channel is either SFW or NSFW, never both.</p>
      {q.isPending ? <p role="status" className="py-3 text-sm text-muted">Loading channels…</p> : q.isError ? <p role="alert" className="py-3 text-sm text-warn">Could not load the channels. Connect Telegram first, then refresh.</p> : !rows.length ? <p className="py-3 text-sm text-muted">No channels yet. Connect Telegram to list them.</p> : (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={allOn} onChange={() => setPicked(allOn ? new Set() : new Set(rows.map((r) => r.telegram_id)))} aria-label="Select all channels" />Select all</label>
            <span className="text-muted">{ids.length ? `${ids.length} selected:` : 'Select channels to change several at once'}</span>
            {ids.length ? (
              <>
                <button type="button" onClick={() => apply(ids, { in_library: true })} className="h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Add to library</button>
                <button type="button" onClick={() => apply(ids, { in_library: false })} className="h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Remove from library</button>
                <button type="button" onClick={() => apply(ids, { content_rating: 'sfw' })} className="h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Mark SFW</button>
                <button type="button" onClick={() => apply(ids, { content_rating: 'nsfw' })} className="h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Mark NSFW</button>
              </>
            ) : null}
          </div>
          <ul className="grid divide-y divide-line rounded-xl border border-line">
            {rows.map((c) => <Row key={c.telegram_id} c={c} picked={picked.has(c.telegram_id)} onPick={() => toggle(c.telegram_id)} apply={(b) => apply([c.telegram_id], b)} />)}
          </ul>
        </>
      )}
    </section>
  )
}

function Row({ c, picked, onPick, apply }: { c: ChannelMark; picked: boolean; onPick: () => void; apply: (b: { in_library?: boolean; content_rating?: Mode; holds?: string }) => void }) {
  const inLib = Boolean(c.in_library)
  return (
    <li className={cn('flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5', !inLib && 'opacity-70')}>
      <input type="checkbox" checked={picked} onChange={onPick} aria-label={`Select ${c.title}`} />
      <div className="min-w-0 flex-1 basis-48"><b className="block truncate text-sm">{c.title}</b><small className="text-xs text-muted">{c.videos.toLocaleString()} videos · {c.photos.toLocaleString()} photos</small></div>
      <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={inLib} onChange={(e) => apply({ in_library: e.target.checked })} aria-label={`${c.title} is in the library`} />In library</label>
      <Segmented<Mode> label={`${c.title} content`} value={c.content_rating} onChange={(v) => apply({ content_rating: v })} options={[{ value: 'sfw', label: 'SFW' }, { value: 'nsfw', label: 'NSFW' }]} />
      <select aria-label={`${c.title} holds`} value={c.holds} onChange={(e) => apply({ holds: e.target.value })} className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm">
        {HOLDS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
      </select>
    </li>
  )
}
