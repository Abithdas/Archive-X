import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { useHardware } from './queries'
import { browserRows, hardwareRows, speedResult, type Row, type SpeedResult } from './system'

type Obj = Record<string, unknown>
const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-10 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60'

const Rows = ({ rows, label }: { rows: Row[]; label: string }) => (
  <dl aria-label={label} className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-2 lg:grid-cols-3">
    {rows.map((r) => <div key={r.label} className="min-w-0 rounded-xl bg-bg p-3.5"><dt className="text-xs text-muted">{r.label}</dt><dd className="mt-1 text-sm font-bold [overflow-wrap:anywhere]">{r.value}</dd></div>)}
  </dl>
)

/** The browser's own capabilities, read from navigator and a probe <video> element. */
function useBrowserRows(): Row[] {
  return useMemo(() => {
    let v: HTMLVideoElement | undefined
    try { v = document.createElement('video') } catch { /* no DOM video */ }
    const can = (t: string) => Boolean(v?.canPlayType?.(t))
    return browserRows({
      ua: navigator.userAgent || '', platform: navigator.platform || '', touchPoints: navigator.maxTouchPoints || 0,
      mp4: can('video/mp4'), webm: can('video/webm'), hls: can('application/vnd.apple.mpegurl'),
      fetch: typeof fetch === 'function', abort: typeof AbortController === 'function', pointer: typeof PointerEvent !== 'undefined',
    })
  }, [])
}

/** System & network: browser compatibility, detected hardware and an on-demand internet speed test. */
export function SystemPanel() {
  const qc = useQueryClient()
  const hw = useHardware()
  const browser = useBrowserRows()
  const [speed, setSpeed] = useState<{ state: 'idle' | 'running' | 'done' | 'failed'; text: string; result?: SpeedResult; error?: string }>({ state: 'idle', text: '' })
  const [refreshing, setRefreshing] = useState(false)

  const refresh = async () => {
    setRefreshing(true)
    try { await qc.invalidateQueries({ queryKey: ['settings', 'hardware'] }) } finally { setRefreshing(false) }
  }
  const runSpeed = async () => {
    setSpeed({ state: 'running', text: 'Testing latency, download and upload…' })
    try {
      const r = await api<Obj>('/api/system/internet-test', { method: 'POST', json: {} })
      if (r.ok === false) throw new ApiError(0, String(r.detail || r.error || 'test failed'))
      const result = speedResult(r)
      setSpeed({ state: 'done', text: result.status, result })
      void qc.invalidateQueries({ queryKey: ['settings', 'hardware'] })   // the test updates the network profile
    } catch (e) { setSpeed({ state: 'failed', text: 'Internet test failed', error: e instanceof ApiError ? e.detail || 'test failed' : String(e) }) }
  }

  const hwError = hw.isError || (hw.data && (hw.data as Obj).ok === false)
  return (
    <div className="grid gap-4">
      <section aria-label="Browser compatibility" className={BOX}>
        <div className="mb-4 flex items-center justify-between"><h3 className="flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="info" className="size-5" /></span>Browser compatibility</h3><span className="hidden text-xs text-faint sm:block">Feature detection &amp; fallback</span></div>
        <Rows rows={browser} label="Browser capabilities" />
        <p className="mt-3 text-xs text-muted">The player always uses the direct stream (no conversion).</p>
      </section>

      <section aria-label="System and network optimization" className={BOX}>
        <div className="mb-2 flex items-center justify-between"><h3 className="flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="gauge" className="size-5" /></span>System &amp; Network Optimization</h3><span className="hidden text-xs text-faint sm:block">Hardware-aware</span></div>
        <p className="mb-4 text-sm text-muted">CPU, GPU, RAM and storage are detected at startup. Hardware encoding is used when verified. Internet testing is on-demand.</p>
        {hwError ? <p role="alert" className="text-sm text-warn">Hardware detection unavailable.</p>
          : hw.isPending ? <p role="status" className="text-sm text-muted">Detecting CPU/GPU/RAM/storage…</p>
          : <Rows rows={hardwareRows(hw.data as Obj)} label="Hardware" />}

        {speed.result ? (
          <div className="mt-4">
            <p className="mb-2 text-xs font-bold uppercase tracking-wide text-muted">Internet speed</p>
            <Rows rows={speed.result.rows} label="Internet speed" />
            <p className="mt-2 text-xs text-muted">{speed.result.note}</p>
          </div>
        ) : speed.state === 'failed' ? <p role="alert" className="mt-4 text-sm text-bad [overflow-wrap:anywhere]">{speed.error}</p> : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void refresh()} disabled={refreshing} className={BTN}>Refresh hardware</button>
          <button type="button" onClick={() => void runSpeed()} disabled={speed.state === 'running'} className={PRIMARY}>Test internet speed</button>
          <span role="status" aria-live="polite" className={cn('text-xs', speed.state === 'done' ? (speed.result?.ok ? 'text-good' : 'text-warn') : speed.state === 'failed' ? 'text-bad' : 'text-muted')}>{speed.text}</span>
        </div>
      </section>
    </div>
  )
}
