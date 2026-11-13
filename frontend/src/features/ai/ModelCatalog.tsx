import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from '@/api/client'
import { cn } from '@/design/cn'
import { STATE_LABEL, anyBusy, modelsSummary, sizeText, stateTone, testLine, type LocalModel, type ModelsReply } from './ai'

const BTN = 'inline-flex h-9 items-center rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
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
                {m.error ? <p role="alert" className="text-xs text-bad">Last download failed: {m.error}</p> : null}
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className={BTN} disabled={testing === m.id || m.state === 'downloading'} onClick={() => void test(m)}>Test</button>
                  {m.installable ? <button type="button" className={PRIMARY} disabled={m.state === 'downloading'} onClick={() => void download(m)}>{m.state === 'downloading' ? 'Downloading…' : 'Download'}</button> : null}
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
