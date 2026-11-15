import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { MatrixLoader } from '@/design/MatrixLoader'
import { Segmented } from '@/design/Segmented'
import { Shimmer } from '@/design/Shimmer'
import { SubtleNumber } from '@/design/SubtleNumber'
import { StatusMark } from '@/design/StatusMark'
import { cn } from '@/design/cn'
import { StepsFeed } from './StepsFeed'
import { bytesText, percentOf, ramNote, setupTotal, speedText, type SetupInfo, type SetupOption } from './ai'

const PRIMARY = 'inline-flex h-11 items-center gap-2 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-50'
const BTN = 'inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const why = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : fb)

/**
 * First-run setup of the local assistant: pick a size, press one button, and the engine and model are downloaded, checked, started and tested.
 * The page shows the real steps and the real log lines of that run.
 */
export function AssistantSetup({ onFinished }: { onFinished: () => void }) {
  const q = useQuery({
    queryKey: ['ai-setup'] as const, queryFn: () => api<SetupInfo>('/api/ai/setup'), retry: false, staleTime: 0,
    refetchInterval: (query) => (query.state.data?.run.state === 'running' ? 1000 : false),
  })
  const info = q.data
  const [model, setModel] = useState<SetupOption['id'] | ''>('')
  const [engine, setEngine] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const chosen = model || info?.recommended || 'balanced'
  const eng = engine || info?.engines[0]?.id || ''
  const run = info?.run
  const opt = info?.models.find((m) => m.id === chosen)
  const engineInfo = info?.engines.find((e) => e.id === eng)
  const running = run?.state === 'running'
  useEffect(() => { if (run?.state === 'done') { const t = setTimeout(onFinished, 1600); return () => clearTimeout(t) } }, [run?.state]) // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setBusy(true); setErr('')
    try { await api('/api/ai/setup/start', { method: 'POST', json: { model: chosen, engine: eng } }); await q.refetch() }
    catch (e) { setErr(why(e, 'Could not start the setup.')) } finally { setBusy(false) }
  }
  const stop = async () => { try { await api('/api/ai/setup/cancel', { method: 'POST', json: {} }); await q.refetch() } catch (e) { setErr(why(e, 'Could not stop.')) } }

  if (q.isPending) return <section aria-label="Set up the assistant" className="rounded-2xl border border-line bg-surface p-6"><Shimmer text="Checking this PC…" /></section>
  if (q.isError || !info) return <section aria-label="Set up the assistant" className="rounded-2xl border border-line bg-surface p-6"><p role="alert" className="text-sm text-bad">Could not check this PC.</p><button type="button" className={cn(BTN, 'mt-3')} onClick={() => void q.refetch()}>Try again</button></section>
  if (!info.supported) return (
    <section aria-label="Set up the assistant" className="rounded-2xl border border-line bg-surface p-6 text-left">
      <h2 className="text-lg font-bold">Set up the assistant</h2>
      <p className="mt-1 text-sm text-muted">The one-click setup is made for 64-bit Windows. On this computer, point Archive X at your own llama-server program and .gguf model in Models, Advanced.</p>
    </section>
  )

  const pct = run ? percentOf(run.bytes_done, run.bytes_total) : 0
  const active = run?.steps.find((s) => s.state === 'active')
  const finished = run?.state === 'done'
  const showRun = run && run.state !== 'idle'

  return (
    <section aria-label="Set up the assistant" className="grid gap-5 rounded-2xl border border-line bg-surface p-5 text-left sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-extrabold tracking-tight">Set up the assistant</h2>
          <p className="mt-1 max-w-2xl text-sm text-muted">It runs on this PC, so nothing you ask leaves it. One press downloads the engine and a model, checks both against fixed checksums, starts it and asks it a test question.</p>
        </div>
        <span className="rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good">NO CLOUD</span>
      </div>

      {!showRun || run.state === 'cancelled' || run.state === 'error' ? (
        <>
          <div role="radiogroup" aria-label="Model size" className="grid gap-3 md:grid-cols-3">
            {info.models.map((m) => {
              const on = m.id === chosen
              const note = ramNote(info.ram_gb, m)
              return (
                <button key={m.id} type="button" role="radio" aria-checked={on} onClick={() => setModel(m.id)} className={cn('grid content-start gap-1.5 rounded-xl border p-4 text-left transition-colors', on ? 'border-accent bg-accent/10' : 'border-line hover:bg-fg/5')}>
                  <span className="flex items-center justify-between gap-2"><b className="text-base">{m.label}</b>{m.recommended ? <span className="rounded-full bg-accent px-2 py-0.5 text-[10.5px] font-bold text-accent-fg">RECOMMENDED</span> : null}</span>
                  <span className="text-xs text-faint">{m.model} · {bytesText(m.size_mb * 1024 ** 2)}</span>
                  <span className="text-sm text-muted">{m.blurb}</span>
                  {note ? <span className="text-xs font-semibold text-warn">{note}</span> : null}
                </button>
              )
            })}
          </div>
          {info.engines.length > 1 ? (
            <div className="flex flex-wrap items-center gap-3">
              <Segmented label="Run it on" value={eng} onChange={setEngine} options={info.engines.map((e) => ({ value: e.id, label: e.label }))} />
              <span className="text-xs text-muted">{engineInfo?.gpu ? 'Uses your graphics card when it can; much quicker on a gaming PC. If it fails to start, switch back to Processor.' : 'Works on every PC.'}</span>
            </div>
          ) : null}
          {run?.state === 'error' || err ? <p role="alert" className="rounded-xl bg-bad/10 p-3 text-sm text-bad [overflow-wrap:anywhere]">{err || run?.error}</p> : null}
          {run?.state === 'cancelled' ? <p role="status" className="text-sm text-muted">Stopped. What was downloaded is kept; pressing Set up again carries on from there.</p> : null}
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className={PRIMARY} disabled={busy} onClick={() => void start()}>{run?.state === 'error' || run?.state === 'cancelled' ? 'Try again' : 'Set up'} · {setupTotal(opt, engineInfo)}</button>
            <span className="text-xs text-muted">Needs about {setupTotal(opt, engineInfo)} of disk space and an internet connection, once.{info.ram_gb ? ` This PC has ${info.ram_gb} GB of memory.` : ''}</span>
          </div>
        </>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="grid content-start gap-4">
            {finished ? (
              <div className="flex items-center gap-3"><span className="scale-150 origin-left"><StatusMark state="done" /></span><div className="ml-3"><b className="block text-base">The assistant is ready</b><span className="text-sm text-muted">Opening the chat…</span></div></div>
            ) : (
              <div className="flex items-center gap-3 text-sm"><MatrixLoader variant="orbit" label="Setting up" /><Shimmer text={active?.label ?? 'Working…'} className="font-semibold" /></div>
            )}
            <StepsFeed label="Setup steps" steps={run.steps} />
          </div>
          <div className="grid content-start gap-3 rounded-xl bg-bg p-4">
            {running && run.bytes_total > 0 ? (
              <div className="grid gap-1.5">
                <div className="flex items-baseline justify-between text-sm"><b><SubtleNumber value={pct} />%</b><span className="text-xs text-muted">{bytesText(run.bytes_done)} of {bytesText(run.bytes_total)}{speedText(run.speed_bps) ? ` · ${speedText(run.speed_bps)}` : ''}</span></div>
                <div role="progressbar" aria-label="Download progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="h-2 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(2, pct)}%` }} /></div>
              </div>
            ) : null}
            <ul aria-label="What it is doing" className="ax-feed grid gap-1 pt-1 text-xs text-muted">
              {run.log.slice(-6).map((line, i, all) => <li key={`${run.log.length - all.length + i}`} className={cn('card-in [overflow-wrap:anywhere]', i === all.length - 1 && 'text-fg')}>{line}</li>)}
            </ul>
            {running ? <button type="button" className={cn(BTN, 'w-fit')} onClick={() => void stop()}>Stop</button> : null}
          </div>
        </div>
      )}
    </section>
  )
}
