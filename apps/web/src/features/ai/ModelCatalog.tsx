import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from '@/api/client'
import { cn } from '@/design/cn'
import { STATE_LABEL, anyBusy, canDelete, modelsSummary, sizeText, stateTone, testLine, variantBadges, type LocalModel, type ModelVariant, type ModelsReply } from './ai'

const BTN = 'inline-flex h-9 items-center rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const SMALL = 'inline-flex h-7 items-center whitespace-nowrap rounded-lg border border-line-2 px-2.5 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'inline-flex h-9 items-center rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50'

/** Every local AI model in one place: what it is, what it powers, whether it is ready, a one-click self-test, and download. */
export function ModelCatalog() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['ai-models'] as const, queryFn: async () => { const r = await api<ModelsReply>('/api/ai/models'); if (!r || !Array.isArray(r.models)) throw new ApiError(0, 'The model list is unavailable.'); return r }, staleTime: 0, retry: false, refetchInterval: (query) => (anyBusy(query.state.data) ? 2000 : false) })
  const [results, setResults] = useState<Record<string, string>>({})
  const [testing, setTesting] = useState<string>('')
  const test = async (m: LocalModel) => {
    setTesting(m.id); setResults((r) => ({ ...r, [m.id]: 'Testing…' }))
    try { const res = await api<{ ok?: boolean; ms?: number; detail?: string }>(`/api/ai/models/${m.id}/test`, { method: 'POST', json: {} }); setResults((r) => ({ ...r, [m.id]: testLine(res) })) }
    catch (e) { setResults((r) => ({ ...r, [m.id]: `Not working: ${e instanceof ApiError && e.detail ? e.detail : 'the test could not run.'}` })) }
    finally { setTesting('') }
  }
  const download = async (m: LocalModel) => {
    setResults((r) => ({ ...r, [m.id]: `Downloading about ${sizeText(m.size_mb)}…` }))
    try { await api(`/api/ai/models/${m.id}/install`, { method: 'POST', json: {} }) } catch (e) { setResults((r) => ({ ...r, [m.id]: e instanceof ApiError && e.detail ? e.detail : 'Could not start the download.' })) }
    await qc.invalidateQueries({ queryKey: ['ai-models'] })
  }
  const [sure, setSure] = useState('')
  const act = async (m: LocalModel, v: ModelVariant, what: 'install' | 'select' | 'delete') => {
    const url = `/api/ai/models/${m.id}/variants/${v.id}${what === 'delete' ? '' : `/${what}`}`
    setSure('')
    setResults((r) => ({ ...r, [m.id]: what === 'install' ? `Downloading the ${v.label.toLowerCase()} size (about ${sizeText(v.size_mb)})…` : what === 'select' ? 'Switching…' : 'Deleting…' }))
    try {
      const res = await api<{ freed_mb?: number }>(url, { method: what === 'delete' ? 'DELETE' : 'POST', ...(what === 'delete' ? {} : { json: {} }) })
      setResults((r) => ({ ...r, [m.id]: what === 'delete' ? `Deleted. ${res?.freed_mb ? `${sizeText(res.freed_mb)} freed.` : 'Space freed.'}` : what === 'select' ? `Now using the ${v.label.toLowerCase()} size.` : '' }))
    } catch (e) { setResults((r) => ({ ...r, [m.id]: e instanceof ApiError && e.detail ? e.detail : 'That did not work.' })) }
    await qc.invalidateQueries({ queryKey: ['ai-models'] })
  }
  const d = q.data
  return (
    <section aria-label="Local AI models" className="rounded-2xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0"><h2 className="text-lg font-bold">Local AI models</h2><p className="text-xs text-muted">Everything runs on this PC. Nothing is sent to a cloud service.</p></div>
        <span role="status" aria-live="polite" className="rounded-full bg-surface-2 px-3 py-1 text-xs font-bold">{q.isError ? 'Could not check the models' : modelsSummary(d)}</span>
      </div>
      {q.isPending ? <p className="text-sm text-muted">Checking local models…</p> : q.isError ? <button type="button" className={PRIMARY} onClick={() => void q.refetch()}>Retry</button> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {d!.models.map((m) => {
            const tone = stateTone(m.state)
            return (
              <article key={m.id} aria-label={m.name} className="grid content-start gap-2 rounded-xl border border-line bg-bg p-3 text-left">
                <div className="flex items-start justify-between gap-2"><div className="min-w-0"><b className="block text-sm">{m.name}</b><small className="text-xs text-muted">{m.detail}</small></div>
                  <span className={cn('shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-bold', tone === 'good' ? 'bg-good/15 text-good' : tone === 'busy' ? 'bg-accent/15 text-accent' : 'bg-warn/15 text-warn')}>{STATE_LABEL[m.state]}</span></div>
                <div className="flex flex-wrap gap-1.5" aria-label={`${m.name} is used by`}>{m.powers.map((p) => <span key={p} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">{p}</span>)}</div>
                <p className="text-xs text-faint">{sizeText(m.size_mb)} · {m.licence}</p>
                {m.hint && m.state !== 'ready' ? <p className="text-xs text-muted">{m.hint}</p> : null}
                {m.variants?.length ? (
                  <ul aria-label={`${m.name} sizes`} className="grid gap-1.5">
                    {m.variants.map((v) => {
                      const all = m.variants ?? []
                      const confirming = sure === `${m.id}/${v.id}`
                      return (
                        <li key={v.id} className={cn('grid gap-1 rounded-lg border p-2', v.selected && v.installed ? 'border-accent/50 bg-accent/5' : 'border-line')}>
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                            <b className="text-[13px]">{v.label}</b>
                            <span className="text-[11px] text-faint">{sizeText(v.size_mb)}</span>
                            {variantBadges(v).map((b) => <span key={b} className={cn('rounded-full px-2 py-0.5 text-[10px] font-bold', b === 'In use' ? 'bg-accent/15 text-accent' : b === 'Recommended' ? 'bg-good/15 text-good' : 'bg-surface-2 text-muted')}>{b}</span>)}
                            {v.fits === false ? <span className="rounded-full bg-warn/15 px-2 py-0.5 text-[10px] font-bold text-warn">Needs more memory</span> : null}
                            </div>
                            <span className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                              {v.busy ? <span role="status" className="text-xs font-semibold text-accent">Downloading {v.progress ?? 0}%</span>
                                : !v.installed ? <button type="button" className={SMALL} disabled={m.busy || m.state === 'needs_package'} onClick={() => void act(m, v, 'install')}>{m.id === 'llm' ? 'Download and use' : 'Download'}</button>
                                : confirming ? <>
                                  <button type="button" className={cn(SMALL, 'border-bad text-bad')} onClick={() => void act(m, v, 'delete')}>Delete for good</button>
                                  <button type="button" className={SMALL} onClick={() => setSure('')}>Keep</button></>
                                : <>
                                  {!v.selected ? <button type="button" className={SMALL} disabled={m.busy} onClick={() => void act(m, v, 'select')}>Use this</button> : null}
                                  {canDelete(v, all) ? <button type="button" className={SMALL} disabled={m.busy} onClick={() => setSure(`${m.id}/${v.id}`)} aria-label={`Delete the ${v.label.toLowerCase()} size of ${m.name}`}>Delete</button> : null}</>}
                            </span>
                          </div>
                          {v.busy ? <div role="progressbar" aria-valuenow={v.progress ?? 0} aria-valuemin={0} aria-valuemax={100} className="h-1 overflow-hidden rounded-full bg-surface-2"><div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${v.progress ?? 0}%` }} /></div> : null}
                          <p className="text-[11px] leading-snug text-muted">{v.note}</p>
                        </li>
                      )
                    })}
                  </ul>
                ) : null}
                {m.error ? <p role="alert" className="text-xs text-bad">Last download failed: {m.error}</p> : null}
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className={BTN} disabled={testing === m.id || m.state === 'downloading'} onClick={() => void test(m)}>Test</button>
                  {m.installable && !m.variants?.length ? <button type="button" className={PRIMARY} disabled={m.state === 'downloading'} onClick={() => void download(m)}>{m.state === 'downloading' ? 'Downloading…' : 'Download'}</button> : null}
                  <span role="status" aria-live="polite" className="min-w-0 text-xs text-muted">{results[m.id] ?? ''}</span>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
