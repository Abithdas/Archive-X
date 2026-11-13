import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/api/client'

const why = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : fb)

interface Local { id: number; name: string; image_url?: string }
interface Remote { provider: string; provider_id: string; name: string; image?: string; profile?: string }
const providerName = (p: string) => (p === 'stashdb' ? 'StashDB' : p === 'theporndb' ? 'ThePornDB' : p)

/** Type a performer name: people already in your library first, then performers available through the metadata APIs (picking one downloads it first). */
export function NamePicker({ onPick, onCancel }: { onPick: (p: { id: number; name: string }) => void; onCancel: () => void }) {
  const [text, setText] = useState('')
  const [local, setLocal] = useState<Local[]>([])
  const [remote, setRemote] = useState<Remote[]>([])
  const [searching, setSearching] = useState(false)
  const [configured, setConfigured] = useState(true)
  const [importing, setImporting] = useState('')
  const [err, setErr] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const seq = useRef(0)
  useEffect(() => () => clearTimeout(timer.current), [])
  const type = (v: string) => {
    setText(v); setErr(''); clearTimeout(timer.current)
    const mine = ++seq.current
    if (v.trim().length < 2) { setLocal([]); setRemote([]); setSearching(false); return }
    setSearching(true)
    timer.current = setTimeout(() => {
      const q = encodeURIComponent(v.trim())
      api<{ items?: Local[] }>(`/api/photos/faces/performers?limit=8&q=${q}`).then((r) => { if (mine === seq.current) setLocal(r.items ?? []) }).catch(() => { if (mine === seq.current) setLocal([]) })
      api<{ items?: Remote[]; configured?: boolean }>(`/api/photos/faces/performers/provider-search?q=${q}`)
        .then((r) => { if (mine === seq.current) { setRemote(r.items ?? []); setConfigured(r.configured !== false) } })
        .catch(() => { if (mine === seq.current) setRemote([]) })
        .finally(() => { if (mine === seq.current) setSearching(false) })
    }, 250)
  }
  const importOne = async (r: Remote) => {
    setImporting(r.provider_id); setErr('')
    try {
      const res = await api<{ ok?: boolean; id?: number; name?: string; detail?: string }>('/api/photos/faces/performers/import', { method: 'POST', json: { provider: r.provider, provider_id: r.provider_id, name: r.name, image: r.image ?? '', profile: r.profile ?? '' } })
      if (!res?.ok || !res.id) throw new ApiError(0, res?.detail || '')
      onPick({ id: res.id, name: res.name ?? r.name })
    } catch (e) { setErr(why(e, 'Could not add that performer.')); setImporting('') }
  }
  const row = 'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-fg/10 disabled:opacity-60'
  const none = text.trim().length >= 2 && !searching && !local.length && !remote.length
  return (
    <div className="grid w-full gap-1">
      <input autoFocus type="search" value={text} onChange={(e) => type(e.target.value)} onKeyDown={(e) => e.key === 'Escape' && onCancel()} placeholder="Type a performer name…" aria-label="Performer name" className="h-9 w-full rounded-lg border border-line-2 bg-bg px-3 text-sm outline-none focus:border-accent/60" />
      {local.length || remote.length || searching || none ? (
        <div role="listbox" aria-label="Performers" className="grid max-h-60 overflow-y-auto rounded-lg border border-line bg-surface p-1 text-left">
          {local.length ? <div className="px-2 pb-0.5 pt-1 text-[10.5px] font-bold uppercase tracking-wider text-faint">In your library</div> : null}
          {local.map((p) => <button key={`l${p.id}`} type="button" role="option" aria-selected="false" onClick={() => onPick(p)} className={row}><img src={p.image_url || `/api/performers/${p.id}/image?w=96`} alt="" className="size-7 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />{p.name}</button>)}
          {remote.length ? <div className="px-2 pb-0.5 pt-2 text-[10.5px] font-bold uppercase tracking-wider text-faint">From metadata providers (added when picked)</div> : null}
          {remote.map((r) => <button key={`${r.provider}:${r.provider_id}`} type="button" role="option" aria-selected="false" disabled={!!importing} onClick={() => void importOne(r)} className={row}>
            <img src={r.image || ''} alt="" referrerPolicy="no-referrer" className="size-7 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
            <span className="min-w-0 flex-1 truncate">{r.name}</span><small className="text-[11px] text-muted">{importing === r.provider_id ? 'Adding…' : providerName(r.provider)}</small></button>)}
          {searching ? <div className="px-2 py-1.5 text-xs text-muted">Searching…</div> : null}
          {none ? <div className="px-2 py-1.5 text-xs text-muted">{configured ? 'No performer found.' : 'No performer in your library. Add a StashDB or ThePornDB key in Settings to search online.'}</div> : null}
        </div>
      ) : null}
      {err ? <p role="alert" className="text-xs text-bad">{err}</p> : null}
      <button type="button" onClick={onCancel} className="w-fit text-xs text-muted hover:underline">Cancel</button>
    </div>
  )
}
