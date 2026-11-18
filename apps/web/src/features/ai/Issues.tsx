import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { cn } from '@/design/cn'

export interface Issue { id: string; area: string; severity: 'high' | 'medium' | 'low'; title: string; count: number; ago: string; cause: string; check: string; evidence: string[] }
export interface Digest { generated_at: string; looked_at: { log_events: number; errors: number; warnings: number }; ok: boolean; issues: Issue[] }
interface DigestPayload { ok: boolean; digest: Digest; text: string }

const BTN = 'inline-flex h-9 items-center rounded-lg border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const SEV = { high: 'bg-bad/15 text-bad', medium: 'bg-warn/15 text-warn', low: 'bg-surface-3 text-muted' } as const

/** How much was looked at, in one line. */
export const lookedAt = (d: Digest) => `Looked at ${d.looked_at.log_events.toLocaleString()} log events (${d.looked_at.errors.toLocaleString()} errors, ${d.looked_at.warnings.toLocaleString()} warnings) and the failed jobs.`

/** The Issues tab: what is going wrong, found by rules from the logs, failed jobs and ignored errors. It needs no model, and its text can be given to any model. */
export function Issues({ onExplain }: { onExplain?: () => void }) {
  const q = useQuery({ queryKey: ['ai-digest'] as const, queryFn: () => api<DigestPayload>('/api/ai/digest'), staleTime: 0, retry: false })
  const text = q.data?.text ?? ''
  const copy = async () => { try { await navigator.clipboard.writeText(text); toast('Copied. Paste it into any local model, or into a message to someone who can help.') } catch { toast.error('Could not copy. Use Download instead.') } }
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: `archive-x-issues-${new Date().toISOString().slice(0, 10)}.txt` })
    a.click()
    URL.revokeObjectURL(url)
  }
  const d = q.data?.digest
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0"><h2 className="text-lg font-bold">Issues</h2><p className="text-xs text-muted">Found by rules from the log, the failed jobs and the errors the app ignores. It only reads: nothing is changed, and no model is needed. A small local model can read the whole list at once.</p></div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={BTN} disabled={q.isFetching} onClick={() => void q.refetch()}>{q.isFetching ? 'Looking…' : 'Check again'}</button>
          <button type="button" className={BTN} disabled={!text} onClick={() => void copy()}>Copy as text</button>
          <button type="button" className={BTN} disabled={!text} onClick={download}>Download</button>
          {onExplain ? <button type="button" className={cn(BTN, 'border-accent text-accent')} onClick={onExplain}>Explain with the local model</button> : null}
        </div>
      </div>
      {q.isPending ? <p role="status" className="py-6 text-center text-sm text-muted">Reading the log…</p>
        : q.isError || !d ? <p role="alert" className="py-6 text-center text-sm text-warn">The issue list could not be made.</p>
        : (
          <>
            <p role="status" className="mb-3 text-xs text-muted">{lookedAt(d)}</p>
            {d.ok ? <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">No problems found in the recent log, failed jobs or ignored errors.</p> : (
              <ul className="grid gap-2" aria-label="Issues">
                {d.issues.map((i) => (
                  <li key={i.id} className="rounded-xl border border-line p-3 text-left">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-bold uppercase', SEV[i.severity])}>{i.severity}</span>
                      <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[11px] font-bold text-muted">{i.area}</span>
                      <b className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">{i.title}</b>
                      <span className="text-xs tabular-nums text-muted">×{i.count.toLocaleString()} · {i.ago}</span>
                    </div>
                    {i.cause ? <p className="mt-1.5 text-sm text-muted">{i.cause}</p> : null}
                    <p className="mt-1 text-sm"><b>Check:</b> {i.check}</p>
                    {i.evidence.length ? <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-bg p-2 text-[11px] text-faint [overflow-wrap:anywhere]">{i.evidence.join('\n')}</pre> : null}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
    </div>
  )
}
