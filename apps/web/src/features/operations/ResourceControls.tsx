import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { queryKeys } from '@/api/hooks'
import { cn } from '@/design/cn'
import { DRAFT_HINT, EXPIRIES, LANE_KEYS, LANE_LABELS, MANUAL_FAILED, MODES, MODE_FAILED, PAUSE_FAILED, clampLimit, isPaused, limitsFrom, manualBody, manualSaved, modeBody, modeChanged, modeOf, pauseBody, pauseDone, pauseLabel, playbackGate, type Lane, type Mode } from './resourcecontrol'

type Obj = Record<string, unknown>
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : fb)

/** Pick how playback, background work and transfers share the machine; Manual mode lets you set each lane's limit. */
export function ResourceControls({ resources }: { resources: Obj }) {
  const qc = useQueryClient()
  const current = modeOf(resources)
  const [draft, setDraft] = useState(false)           // Manual chosen but not saved yet
  const [limits, setLimits] = useState<Record<Lane, number | string>>(() => limitsFrom(resources))
  const [gate, setGate] = useState(() => playbackGate(resources))
  const [expiry, setExpiry] = useState(0)
  const [busy, setBusy] = useState(false)
  const paused = isPaused(resources)
  const shown: Mode = draft ? 'manual' : current

  // Follow the server while the operator is not editing; a draft belongs to the operator and live polling must not overwrite it.
  useEffect(() => { if (!draft) { setLimits(limitsFrom(resources)); setGate(playbackGate(resources)) } }, [resources, draft])
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ['resources'] }), qc.invalidateQueries({ queryKey: queryKeys.status })])
  const post = (body: object) => api<{ ok?: boolean; detail?: string; resources?: { control?: { mode_label?: string } } }>('/api/operations/resources', { method: 'POST', json: body })

  const choose = async (mode: Mode) => {
    if (mode === 'manual') return void setDraft(true)   // nothing is sent until the limits are saved
    setDraft(false); setBusy(true)
    try {
      const r = await post(modeBody(mode))
      if (r?.ok === false) throw new ApiError(0, r.detail || MODE_FAILED)
      toast(modeChanged(r, mode))
    } catch (e) { toast(reason(e, MODE_FAILED)) } finally { setBusy(false); void refresh() }
  }
  const saveManual = async () => {
    setBusy(true)
    try {
      const r = await post(manualBody(limits, gate, expiry))
      if (r?.ok === false) throw new ApiError(0, r.detail || MANUAL_FAILED)
      setDraft(false)
      toast(manualSaved(expiry))
    } catch (e) { toast(reason(e, MANUAL_FAILED)) } finally { setBusy(false); void refresh() }   // on failure the edits (and the draft) are kept
  }
  const togglePause = async () => {
    setBusy(true)
    try {
      const r = await post(pauseBody(paused))
      if (r?.ok === false) throw new ApiError(0, r.detail || PAUSE_FAILED)
      toast(pauseDone(paused))
    } catch (e) { toast(reason(e, PAUSE_FAILED)) } finally { setBusy(false); void refresh() }
  }

  return (
    <div className="mt-4 grid gap-3 border-t border-line pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-2 text-sm font-semibold">Mode
          <select value={shown} disabled={busy} onChange={(e) => void choose(e.target.value as Mode)} aria-label="Resource mode" className="h-10 rounded-xl border border-line-2 bg-surface px-3 text-sm font-semibold">
            {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        <button type="button" disabled={busy} onClick={() => void togglePause()} className={cn(BTN, paused && 'border-bad text-bad')}>{pauseLabel(paused)}</button>
      </div>

      {shown === 'manual' ? (
        <section aria-label="Manual resource limits" className="grid gap-3 rounded-2xl border border-line p-3.5">
          <div><h4 className="text-sm font-bold">Manual Resource Limits</h4><p className="text-xs text-muted">Temporary changes can be allowed to expire automatically.</p></div>
          {draft ? <p role="status" className="rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn">{DRAFT_HINT}</p> : null}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {LANE_KEYS.map((k) => (
              <label key={k} className="grid gap-1 text-xs font-semibold text-muted">{LANE_LABELS[k]}
                <input type="number" min={1} max={8} inputMode="numeric" value={limits[k]} onChange={(e) => { setDraft(true); setLimits((l) => ({ ...l, [k]: e.target.value })) }} onBlur={() => setLimits((l) => ({ ...l, [k]: clampLimit(l[k]) }))} className="h-10 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3 text-[15px] text-fg" />
              </label>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={gate} onChange={(e) => { setDraft(true); setGate(e.target.checked) }} className="size-4" />Pause transfer-heavy background work while video playback is active</label>
          <div className="flex flex-wrap items-center gap-2">
            <select value={expiry} onChange={(e) => setExpiry(Number(e.target.value))} aria-label="Keep these limits for" className="h-9 rounded-xl border border-line-2 bg-surface px-3 text-sm">{EXPIRIES.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}</select>
            <button type="button" disabled={busy} onClick={() => void saveManual()} className="h-9 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50">Save &amp; use Manual mode</button>
            <button type="button" disabled={busy || !draft} onClick={() => { setDraft(false); void refresh() }} className={BTN}>Cancel changes</button>
            <button type="button" disabled={busy} onClick={() => void choose('automatic')} className={BTN}>Return to Automatic</button>
          </div>
        </section>
      ) : null}
    </div>
  )
}
