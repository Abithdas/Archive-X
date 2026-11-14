import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { Switch } from '@/design/Switch'
import { cn } from '@/design/cn'
import { useMetadataSettings, useProviderDashboard, useScanSchedule } from './queries'
import { DEFAULT_WINDOW, KEYS, keyPlaceholder, keysBody, providerCards, railRange, windowBody, windowFrom, windowProblem, windowSaved, windowStatus, type KeyKind, type ScanWindow } from './providers'

type Obj = Record<string, unknown>
const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const INPUT = 'h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-10 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60'
const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)
type Test = { state: 'idle' | 'testing' | 'ok' | 'fail'; text: string }

/** Metadata: provider credentials, the daily scan window and provider status. */
export function MetadataPanel() {
  const qc = useQueryClient()
  const meta = useMetadataSettings()
  const sched = useScanSchedule()
  const prov = useProviderDashboard()
  const m = (meta.data ?? {}) as Obj

  // ---- keys
  const [vals, setVals] = useState<Record<KeyKind, string>>({ tpdb: '', stash: '' })
  const [tests, setTests] = useState<Record<KeyKind, Test>>({ tpdb: { state: 'idle', text: '' }, stash: { state: 'idle', text: '' } })
  const [keyMsg, setKeyMsg] = useState('')
  const msgTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(msgTimer.current), [])
  const masked: Record<KeyKind, unknown> = { tpdb: m.tpdb_token, stash: m.stashdb_api_key }
  const refreshKeys = () => qc.invalidateQueries({ queryKey: ['settings'] })
  const saveKeys = async () => {
    clearTimeout(msgTimer.current)
    try {
      const r = await api<Obj>('/api/settings/metadata', { method: 'POST', json: keysBody(vals.tpdb, vals.stash) })
      if (r.ok === false) throw new ApiError(0, String(r.detail ?? ''))
      setKeyMsg('Saved.')
      setVals({ tpdb: '', stash: '' })
      await refreshKeys()
    } catch (e) { setKeyMsg(`Error: ${err(e, '')}`) }
    msgTimer.current = setTimeout(() => setKeyMsg(''), 2500)
  }
  const test = async (kind: KeyKind) => {
    const set = (t: Test) => setTests((x) => ({ ...x, [kind]: t }))
    set({ state: 'testing', text: 'Testing…' })
    try {
      const typed = vals[kind].trim()
      if (typed) { await api('/api/settings/metadata', { method: 'POST', json: { [KEYS[kind].field]: typed } }); setVals((v) => ({ ...v, [kind]: '' })); void refreshKeys() }
      const r = await api<Obj>(KEYS[kind].check, { method: 'POST', json: {} })
      set(r.ok ? { state: 'ok', text: 'Working' } : { state: 'fail', text: String(r.error || 'Failed') })
    } catch (e) { set({ state: 'fail', text: err(e, 'Failed') }) }
  }
  const status = (kind: KeyKind): Test => (tests[kind].state !== 'idle' ? tests[kind] : masked[kind] ? { state: 'ok', text: 'Saved' } : tests[kind])

  // ---- scan window
  const [win, setWin] = useState<ScanWindow>(DEFAULT_WINDOW)
  const [winMsg, setWinMsg] = useState('')
  const winLoaded = useRef(false)
  useEffect(() => { if (sched.data && !winLoaded.current) { winLoaded.current = true; const w = windowFrom(sched.data); setWin(w); setWinMsg(windowStatus(w)) } }, [sched.data])
  const saveWindow = async () => {
    const p = windowProblem(win)
    if (p) return void setWinMsg(p)
    setWinMsg('Saving…')
    try {
      const r = await api<Obj>('/api/settings/scan-schedule', { method: 'POST', json: windowBody(win) })
      if (!r.ok) throw new ApiError(0, String(r.detail ?? 'Could not save scan window'))
      setWinMsg(windowSaved(windowFrom(r)))
      toast('Scan window saved')
      winLoaded.current = false
      await qc.invalidateQueries({ queryKey: ['settings', 'scan-schedule'] })
    } catch (e) { setWinMsg(err(e, 'Could not save scan window')) }
  }

  return (
    <div className="grid gap-4">
      <section aria-label="Metadata" className={BOX}>
        <div className="mb-4 flex items-center justify-between"><h3 className="flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="database" className="size-5" /></span>Metadata</h3><span className="hidden text-xs text-faint sm:block">Provider credentials and local-first enrichment</span></div>
        <div className="rounded-2xl border border-line p-4">
          <h4 className="mb-3 flex items-center gap-2 text-sm font-bold"><Icon name="database" className="size-4 text-accent" />Metadata provider authentication (ThePornDB / StashDB)</h4>
          <div className="grid gap-4">
            {(['tpdb', 'stash'] as const).map((k) => {
              const st = status(k)
              return (
                <div key={k} className="grid gap-1.5">
                  <label htmlFor={`meta-${k}`} className="text-xs font-semibold text-muted">{KEYS[k].label} <span className="font-normal text-faint">(saved in the persistent auth/provider_keys.json; blank keeps the saved {KEYS[k].noun})</span></label>
                  <div className="flex flex-wrap items-center gap-2">
                    <input id={`meta-${k}`} type="password" value={vals[k]} onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} placeholder={keyPlaceholder(k, masked[k])} autoComplete="off" className={cn(INPUT, 'min-w-[200px] flex-1')} />
                    <button type="button" onClick={() => void test(k)} disabled={st.state === 'testing'} aria-label={`Test ${k === 'tpdb' ? 'ThePornDB' : 'StashDB'}`} className={BTN}>Test</button>
                    <span role="status" aria-live="polite" className={cn('flex items-center gap-1 text-xs', st.state === 'ok' ? 'text-good' : st.state === 'fail' ? 'text-bad' : 'text-muted')}>{st.state === 'ok' ? <Icon name="check" className="size-3.5" /> : st.state === 'fail' ? <Icon name="close" className="size-3.5" /> : null}{st.text}</span>
                  </div>
                </div>
              )
            })}
            <div className="flex flex-wrap items-center gap-3"><button type="button" onClick={() => void saveKeys()} className={PRIMARY}>Save all keys</button><span role="status" aria-live="polite" className="text-xs text-muted">{keyMsg}</span></div>
          </div>
        </div>

        <div className="mt-4 rounded-2xl border border-line p-4">
          <h4 className="text-sm font-bold">Metadata &amp; Assets scan window</h4>
          <p className="mb-3 mt-1 text-xs text-muted">Manual scans resume from saved progress. When enabled, scan work pauses outside this local-time window.</p>
          <div className="mb-3 flex items-center gap-3"><Switch label="Restrict scanning to a daily time window" checked={win.enabled} onChange={(v) => setWin((w) => ({ ...w, enabled: v }))} /><span className="text-sm">Restrict scanning to a daily time window</span></div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="grid gap-1.5 text-xs font-semibold text-muted">Start time<input type="time" value={win.start} onChange={(e) => setWin((w) => ({ ...w, start: e.target.value }))} className={cn(INPUT, 'w-36')} /></label>
            <label className="grid gap-1.5 text-xs font-semibold text-muted">End time<input type="time" value={win.end} onChange={(e) => setWin((w) => ({ ...w, end: e.target.value }))} className={cn(INPUT, 'w-36')} /></label>
            <button type="button" onClick={() => void saveWindow()} className={PRIMARY}>Save scan window</button>
            <span role="status" aria-live="polite" className="text-xs text-muted">{winMsg}</span>
          </div>
        </div>

        <ProviderRail loading={prov.isPending} failed={prov.isError} data={prov.data} />
      </section>
    </div>
  )
}

/** Provider status: a horizontal rail of cards with previous/next controls and a "1–4 of N" range, as in the classic page. */
function ProviderRail({ loading, failed, data }: { loading: boolean; failed: boolean; data: Obj | undefined }) {
  const cards = providerCards(data)
  const track = useRef<HTMLDivElement>(null)
  const [range, setRange] = useState('')
  const [edge, setEdge] = useState({ start: true, end: true })
  const update = useCallback(() => {
    const t = track.current
    if (!t) return
    const box = t.getBoundingClientRect()
    const idx = Array.from(t.children).map((c, i) => ({ i: i + 1, r: c.getBoundingClientRect() })).filter(({ r }) => r.right > box.left + 1 && r.left < box.right - 1).map((x) => x.i).slice(0, 4)
    setRange(railRange(idx, t.children.length))
    setEdge({ start: t.scrollLeft <= 16, end: t.scrollLeft + t.clientWidth >= t.scrollWidth - 1 })
  }, [])
  useEffect(() => { update(); window.addEventListener('resize', update); return () => window.removeEventListener('resize', update) }, [update, cards.length])
  const step = (dir: 1 | -1) => {
    const t = track.current, first = t?.firstElementChild as HTMLElement | null
    if (!t || !first) return
    const gap = parseFloat(getComputedStyle(t).columnGap) || 10
    const w = first.getBoundingClientRect().width + gap
    const visible = Math.max(1, Math.floor((t.clientWidth + gap) / w))
    t.scrollBy({ left: dir * Math.max(1, visible - 1) * w, behavior: 'smooth' })
  }
  return (
    <div className="mt-4 rounded-2xl border border-line p-4">
      <h4 className="mb-3 text-sm font-bold">Provider status</h4>
      {failed ? <p className="text-sm text-muted">Provider status unavailable.</p> : loading ? <p className="text-sm text-muted" role="status">Loading provider status…</p> : (
        <>
          <div className="mb-2 flex items-center gap-1.5">
            <span role="status" aria-live="polite" className="mr-auto text-xs text-muted">{range}</span>
            <button type="button" aria-label="Previous provider status cards" title="Previous cards" disabled={edge.start} onClick={() => step(-1)} className="grid size-8 place-items-center rounded-lg border border-line-2 text-lg disabled:opacity-40">‹</button>
            <button type="button" aria-label="Next provider status cards" title="Next cards" disabled={edge.end} onClick={() => step(1)} className="grid size-8 place-items-center rounded-lg border border-line-2 text-lg disabled:opacity-40">›</button>
          </div>
          <div ref={track} tabIndex={0} role="list" aria-label="Provider status cards" onScroll={update}
            onKeyDown={(e) => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); step(e.key === 'ArrowRight' ? 1 : -1) } }}
            className="flex snap-x snap-mandatory gap-2.5 overflow-x-auto overscroll-x-contain pb-2">
            {cards.map((c) => (
              <div key={c.key} role="listitem" className="min-w-[230px] shrink-0 snap-start basis-[calc((100%-30px)/4)] rounded-xl bg-bg p-3.5 max-[900px]:basis-[calc((100%-10px)/2)] max-[560px]:basis-[85%]">
                <p className="text-xs font-semibold text-muted">{c.title}</p>
                <p className="mt-1 text-[15px] font-bold">{c.value}</p>
                <p className="mt-1 text-[11px] text-muted [overflow-wrap:anywhere]">{c.note}{c.error ? <><br /><span title={c.error} className="text-warn">{c.error}</span></> : null}</p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
