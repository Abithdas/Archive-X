/** Pure logic for the Telegram Job Manager console (the live CLI view of a Media Manager / Telegram transfer job). */

interface ConsoleLine { n: number; t: string }
export interface ConsoleState {
  running?: boolean; error?: string | null; seq?: number; lines?: ConsoleLine[]; live?: string; prompt?: string; mode_name?: string
  completed?: number | null; total?: number | null; operation?: string
}
export const MAX_CHARS = 400_000
const POLL_RUNNING_MS = 700
const POLL_IDLE_MS = 2500
const CONSOLE_JOB_TYPES = ['MEDIA_MANAGER', 'TELEGRAM_TRANSFER']
export const hasConsole = (j: { type?: unknown }) => CONSOLE_JOB_TYPES.includes(String(j.type ?? ''))

export const STOP_COPY = { title: 'Stop Telegram job', ok: 'Stop job', text: 'Stop the Telegram job? Work already verified is kept; it can resume after you choose Resume.' }
export const NOT_WAITING = 'The Media Manager is no longer waiting for input.'

export const consoleTitle = (r: Pick<ConsoleState, 'mode_name'>) => `Telegram Job Manager${r.mode_name ? ` — ${r.mode_name}` : ''}`
export const countsText = (r: Pick<ConsoleState, 'total' | 'completed'>) => (r.total ? `${(r.completed || 0).toLocaleString()} / ${r.total.toLocaleString()} — ${Math.round((100 * (r.completed || 0)) / r.total)}%` : '')
export function jobLine(r: ConsoleState): string {
  if (r.running) { const c = countsText(r); return `Running${c ? ` · ${c}` : ''}` }
  if (r.error) return `Stopped with an error: ${r.error}`
  return r.seq ? 'Finished.' : 'Choose a workflow and press Start.'
}
export const percent = (r: Pick<ConsoleState, 'total' | 'completed'>) => (Number(r.total) > 0 ? Math.max(0, Math.min(100, Math.round((Number(r.completed || 0) * 100) / Number(r.total)))) : null)
export const liveOperation = (r: ConsoleState) => r.operation || r.live || 'Processing'

/** Fold a poll into the text shown: a lower seq than before means the log restarted; the text is capped at its newest MAX_CHARS. */
export function appendLines(text: string, seq: number, r: ConsoleState): { text: string; seq: number } {
  let t = text, s = seq
  if (typeof r.seq === 'number' && r.seq < s) { t = ''; s = 0 }
  const lines = r.lines ?? []
  if (lines.length) {
    const chunk = lines.map((l) => l.t).join('\n')
    t = t ? (t.endsWith('\n') ? t + chunk : `${t}\n${chunk}`) : chunk
    if (t.length > MAX_CHARS) t = t.slice(-MAX_CHARS)
    s = lines[lines.length - 1]!.n
  }
  return { text: t, seq: s }
}
export type Panel = { kind: 'prompt'; body: string; placeholder: string; canAnswer: boolean } | { kind: 'finished'; title: string; help: string }
export function panelFor(r: ConsoleState): Panel {
  const prompt = String(r.prompt || '').trim()
  if (r.running || prompt) return { kind: 'prompt', body: prompt || r.live || r.operation || 'Processing…', placeholder: prompt ? 'Enter your answer…' : 'Waiting…', canAnswer: Boolean(prompt) }
  return { kind: 'finished', title: r.error ? 'Media Manager stopped' : 'Media Manager finished', help: String(r.error || 'The CLI workflow is no longer active.') }
}
export const answerText = (v: string) => v.trim()
export const nextDelay = (running: boolean) => (running ? POLL_RUNNING_MS : POLL_IDLE_MS)
export const pollUrl = (since: number) => `/api/telegram-transfer?since=${since}`

/** When the interactive CLI printed nothing for this job (a background job, or the app restarted since), the job's own activity log is shown instead. */
export const FALLBACK_NOTE = 'The interactive CLI has no output for this job (it ran in the background or before this session). Showing the job activity log instead.'
export interface LogItem { id?: number | string; level?: string; message?: string; operation?: string; created_at?: string }
export function activityText(items: readonly LogItem[], fmt: (iso: string) => string): string {
  return [...items]
    .sort((a, b) => Number(a.id ?? 0) - Number(b.id ?? 0))
    .map((x) => {
      const msg = String(x.message || x.operation || '').trim()
      if (!msg) return ''
      const when = x.created_at ? fmt(x.created_at) : ''
      return `${when ? `${when}  ` : ''}${String(x.level || 'INFO').toUpperCase().padEnd(5)} ${msg}`
    })
    .filter(Boolean)
    .join('\n')
}
export const jobLogUrl = (id: string) => `/api/jobs/${encodeURIComponent(id)}?activity_limit=300`
