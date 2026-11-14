import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { ModelCatalog } from './ModelCatalog'
import { CHAT_FAILED, DIAGNOSTICS_PROMPT, EMPTY_HISTORY, JOBS_PROMPT, LIBRARY_QUICK, NEED_QUERY, QUICK, RECOMMEND_PROMPT, SAVED, SAVE_FAILED, START_FAILED, STARTED, STARTING, STOPPED, STOP_FAILED, TABS, TEST_FAILED, TESTING, TOOLS_OFF, TOOLS_ON, TOOLS_WAIT, chatBody, configBody, configFrom, header, libraryPrompt, messageTime, readiness, replyMeta, testPassed, type AiStatus, type AiTab, type ChatReply, type Config, type Message } from './ai'

const CARD = 'rounded-2xl border border-line bg-surface p-4'
const BTN = 'inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)
type Row = Message & { pending?: boolean }

function Head({ title, blurb, children }: { title: string; blurb: string; children?: React.ReactNode }) {
  return <div className="mb-3 flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h2 className="text-lg font-bold">{title}</h2><p className="text-xs text-muted">{blurb}</p></div>{children}</div>
}
const Empty = ({ children }: { children: React.ReactNode }) => <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">{children}</p>

/** Local, private AI assistant: chat, library/diagnostic/job/recommendation analyses, and local model setup. */
export function AiPage() {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['ai-status'] as const, queryFn: () => api<AiStatus>('/api/ai/status'), staleTime: 0, retry: false })
  const history = useQuery({ queryKey: ['ai-history'] as const, queryFn: () => api<{ messages?: Message[] }>('/api/ai/history'), staleTime: Infinity, retry: false })
  const tools = useQuery({ queryKey: ['ai-tools'] as const, queryFn: () => api<{ local_only?: boolean }>('/api/ai/tools'), staleTime: 60_000, retry: false })
  const [tab, setTab] = useState<AiTab>('assistant')
  const [rows, setRows] = useState<Row[]>([])
  const seeded = useRef(false)
  const [input, setInput] = useState('')
  const [asking, setAsking] = useState(false)
  const [libQ, setLibQ] = useState('')
  const [results, setResults] = useState<Partial<Record<AiTab, string>>>({})
  const [working, setWorking] = useState<Partial<Record<AiTab, boolean>>>({})
  const log = useRef<HTMLDivElement>(null)

  useEffect(() => { if (!seeded.current && history.data) { seeded.current = true; setRows(history.data.messages ?? []) } }, [history.data])
  useEffect(() => { const el = log.current; if (el) el.scrollTop = el.scrollHeight }, [rows])

  /** Sends one chat message; the exchange is shown in the Assistant history whichever tab asked. */
  const ask = useCallback(async (message: string): Promise<string | null> => {
    const text = message.trim()
    if (!text) return null
    setRows((r) => [...r, { role: 'user', content: text, created_at: 'just now' }, { role: 'assistant', content: 'Analyzing local Archive X data…', created_at: 'working…', pending: true }])
    try {
      const r = await api<ChatReply>('/api/ai/chat', { method: 'POST', json: chatBody(text, location.pathname + location.search) })
      if (!r || r.detail) throw new ApiError(0, r?.detail || CHAT_FAILED)
      setRows((cur) => [...cur.filter((m) => !m.pending), { role: 'assistant', content: r.text ?? '', created_at: replyMeta(r.model).replace('Archive X Local AI · ', '') }])
      return r.text ?? ''
    } catch (e) {
      setRows((cur) => [...cur.filter((m) => !m.pending), { role: 'assistant', content: reason(e, CHAT_FAILED), created_at: '' }])
      return null
    }
  }, [])
  const send = async (text: string) => { setAsking(true); try { await ask(text) } finally { setAsking(false) } }
  const analyse = useCallback(async (t: AiTab, prompt: string, empty: string) => {
    setWorking((w) => ({ ...w, [t]: true }))
    const out = await ask(prompt)
    setResults((r) => ({ ...r, [t]: out || empty })); setWorking((w) => ({ ...w, [t]: false }))
  }, [ask])
  const diag = () => analyse('diagnostics', DIAGNOSTICS_PROMPT, 'No diagnostics analysis available.')
  const jobs = () => analyse('jobs', JOBS_PROMPT, 'No job analysis available.')
  const quick = (p: string) => { setTab('assistant'); void send(p) }
  const open = (t: AiTab) => { setTab(t); if (t === 'models') void status.refetch(); if (t === 'diagnostics') void diag(); if (t === 'jobs') void jobs() }
  const librarySearch = async () => {
    const q = libQ.trim()
    if (!q) return void toast(NEED_QUERY)
    setWorking((w) => ({ ...w, library: true }))
    const out = await ask(libraryPrompt(q))
    setResults((r) => ({ ...r, library: out || 'No result' })); setWorking((w) => ({ ...w, library: false }))
  }

  const h = header(status.isError ? { configured: false } : status.data)
  const hdr = status.isError ? { ...h, state: 'AI status unavailable', model: 'Local AI service unavailable' } : h
  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead eyebrow="Local intelligence" title="AI Assistance" blurb="Private, local AI for Archive X library intelligence, diagnostics, jobs and recommendations.">
        <div className="flex items-center gap-2.5 rounded-xl border border-line px-3 py-2" role="status" aria-live="polite">
          <span aria-hidden="true" className={cn('size-2.5 rounded-full', hdr.ready ? 'bg-good' : 'bg-warn')} />
          <div className="text-left"><strong className="block text-sm">{hdr.state}</strong><small className="text-xs text-muted">{hdr.model}</small></div>
        </div>
      </PageHead>
      <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="AI Assistance sections" className="flex gap-1.5 overflow-x-auto md:grid md:content-start">
          {TABS.map((t) => (
            <button key={t.value} type="button" aria-current={tab === t.value ? 'page' : undefined} onClick={() => open(t.value)} className={cn('shrink-0 rounded-xl border px-3 py-2 text-left', tab === t.value ? 'border-accent bg-accent/10' : 'border-transparent hover:bg-fg/5')}>
              <b className="block text-sm">{t.label}</b><small className="text-[11px] text-muted">{t.blurb}</small>
            </button>
          ))}
        </nav>
        <main className="min-w-0">
          {tab === 'assistant' ? (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
              <div className={CARD}>
                <Head title="Archive X Assistant" blurb="Uses only local model inference and controlled Archive X read-only tools."><span className="rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good">LOCAL ONLY</span></Head>
                <div ref={log} role="log" aria-label="Assistant conversation" className="mb-3 grid max-h-[50vh] min-h-40 content-start gap-2 overflow-y-auto">
                  {!rows.length ? <Empty>{history.isPending ? 'Loading the conversation…' : EMPTY_HISTORY}</Empty> : rows.map((m, i) => (
                    <div key={i} className={cn('rounded-xl p-3 text-left text-sm', m.role === 'user' ? 'ml-6 bg-accent/10' : 'mr-6 bg-surface-2')}>
                      <div className="mb-1 text-[11px] text-muted">{m.role === 'user' ? messageTime(m) : `Archive X Local AI${m.created_at ? ` · ${m.created_at}` : ''}`}</div>
                      <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">{m.content}</div>
                    </div>
                  ))}
                </div>
                <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={3} placeholder="Ask Archive X anything…" aria-label="Message" onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (input.trim() && !asking) { const t = input; setInput(''); void send(t) } } }} className="w-full resize-y rounded-xl border border-line-2 bg-bg p-3 text-sm" />
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted">{tools.data ? (tools.data.local_only ? TOOLS_ON : TOOLS_OFF) : tools.isError ? TOOLS_OFF : TOOLS_WAIT}</span>
                  <button type="button" className={PRIMARY} disabled={asking || !input.trim()} onClick={() => { const t = input; setInput(''); void send(t) }}>Ask</button></div>
              </div>
              <div className={CARD}>
                <Head title="Quick actions" blurb="Bounded questions that use Archive X's local data." />
                <div className="grid gap-2">{QUICK.map((q) => (
                  <button key={q.title} type="button" disabled={asking} onClick={() => quick(q.prompt)} className="flex items-center gap-3 rounded-xl border border-line p-3 text-left hover:bg-fg/5 disabled:opacity-60">
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-accent"><Icon name={q.icon} className="size-4" /></span>
                    <span className="min-w-0 flex-1"><b className="block text-sm">{q.title}</b><small className="text-xs text-muted">{q.sub}</small></span><span aria-hidden="true" className="text-muted">›</span>
                  </button>))}</div>
              </div>
            </div>
          ) : null}
          {tab === 'library' ? (
            <div className="grid gap-4">
              <div className={CARD}>
                <Head title="Library Intelligence" blurb="Ask the local model to inspect the published library without bypassing the publication boundary." />
                <div className="mb-3 flex flex-wrap gap-2"><input value={libQ} onChange={(e) => setLibQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void librarySearch()} aria-label="Library question" placeholder="e.g. find videos related to a performer, studio or title" className="h-10 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3 text-sm" /><button type="button" className={PRIMARY} disabled={working.library} onClick={() => void librarySearch()}>Search with AI</button></div>
                <div className="grid gap-2 sm:grid-cols-2">{LIBRARY_QUICK.map((q) => <button key={q.title} type="button" disabled={working.library} onClick={() => { setWorking((w) => ({ ...w, library: true })); void ask(q.prompt).then((out) => { setResults((r) => ({ ...r, library: out || 'No result' })); setWorking((w) => ({ ...w, library: false })) }) }} className="rounded-xl border border-line p-3 text-left hover:bg-fg/5 disabled:opacity-60"><b className="block text-sm">{q.title}</b><small className="text-xs text-muted">{q.sub}</small></button>)}</div>
              </div>
              <div className={CARD} aria-live="polite">{working.library ? <p className="text-sm text-muted">Analyzing local Archive X data…</p> : results.library ? <p className="whitespace-pre-wrap text-left text-sm [overflow-wrap:anywhere]">{results.library}</p> : <Empty>Run a library intelligence action to see the result here.</Empty>}</div>
            </div>
          ) : null}
          {tab === 'diagnostics' || tab === 'jobs' || tab === 'recommendations' ? (() => {
            const meta = { diagnostics: ['Diagnostics', 'Explain current operational evidence without exposing raw logs to the model by default.', 'Refresh analysis', diag, 'Analyzing current diagnostics…'], jobs: ['Jobs', 'Understand active work, dependencies and blockers using the existing JobManager state.', 'Refresh analysis', jobs, 'Analyzing jobs…'], recommendations: ['Recommendations', 'Recommendations are advisory; protected metadata overrides and mutations remain operator-controlled.', 'Generate', () => analyse('recommendations', RECOMMEND_PROMPT, 'No recommendations available.'), 'Generating recommendations…'] } as const
            const [title, blurb, label, run, busy] = meta[tab]
            return (
              <div className={CARD}>
                <Head title={title} blurb={blurb}><button type="button" className={BTN} disabled={working[tab]} onClick={() => void run()}>{label}</button></Head>
                <div aria-live="polite" className="whitespace-pre-wrap text-left text-sm [overflow-wrap:anywhere]">{working[tab] ? busy : results[tab] ?? 'No analysis requested.'}</div>
              </div>
            )
          })() : null}
          {tab === 'models' ? <Models status={status.data} onChanged={() => qc.invalidateQueries({ queryKey: ['ai-status'] })} /> : null}
        </main>
      </div>
    </section>
  )
}

function Models({ status, onChanged }: { status: AiStatus | undefined; onChanged: () => Promise<unknown> }) {
  const [cfg, setCfg] = useState<Config>(() => configFrom(status))
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const filled = useRef(Boolean(status))
  useEffect(() => { if (status && !filled.current) { filled.current = true; setCfg(configFrom(status)) } }, [status])
  const set = <K extends keyof Config>(k: K, v: Config[K]) => setCfg((c) => ({ ...c, [k]: v }))
  const run = async (path: string, body: object, pending: string, ok: (r: Record<string, unknown>) => string, fail: string) => {
    setBusy(true); setMsg(pending)
    try {
      const r = await api<Record<string, unknown>>(path, { method: 'POST', json: body })
      setMsg(r && r.ok ? ok(r) : String(r?.detail || fail))
    } catch (e) { setMsg(reason(e, fail)) } finally { setBusy(false); await onChanged(); }
  }
  const field = (label: string, k: keyof Config, ph = '', num?: [number, number]) => (
    <label className="grid gap-1 text-left text-xs font-semibold text-muted">{label}
      <input value={String(cfg[k])} onChange={(e) => set(k, (num ? Number(e.target.value) : e.target.value) as never)} type={num ? 'number' : 'text'} min={num?.[0]} max={num?.[1]} placeholder={ph} className="h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm font-normal text-fg" />
    </label>
  )
  return (
    <div className="grid gap-4">
      <ModelCatalog />
      <div className="grid gap-4 xl:grid-cols-2">
      <div className={CARD}>
        <Head title="Local model" blurb="Recommended baseline for this application: Qwen3 4B Instruct 2507, Q4_K_M."><span className="rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good">NO CLOUD API</span></Head>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">{field('llama-server executable', 'runtime_path', 'Path to llama-server.exe')}</div>
          <div className="sm:col-span-2">{field('GGUF model file', 'model_path', 'Path to Qwen3-4B-Instruct-2507-Q4_K_M.gguf')}</div>
          {field('Model id', 'model_id')}{field('Local endpoint', 'endpoint')}{field('Context tokens', 'context', '', [1024, 16384])}{field('GPU layers', 'gpu_layers', '', [0, 128])}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={BTN} disabled={busy} onClick={() => void run('/api/ai/config', configBody(cfg), 'Saving…', () => SAVED, SAVE_FAILED)}>Save</button>
          <button type="button" className={PRIMARY} disabled={busy} onClick={() => void run('/api/ai/runtime/start', {}, STARTING, () => STARTED, START_FAILED)}>Start local model</button>
          <button type="button" className={BTN} disabled={busy} onClick={() => void run('/api/ai/runtime/stop', {}, 'Stopping…', () => STOPPED, STOP_FAILED)}>Stop</button>
          <button type="button" className={BTN} disabled={busy} onClick={() => void run('/api/ai/test', {}, TESTING, (r) => testPassed(r.latency_ms, r.text), TEST_FAILED)}>Test model</button>
        </div>
        <p role="status" aria-live="polite" className="mt-2 text-sm text-muted">{msg}</p>
      </div>
      <div className={CARD}>
        <Head title="Readiness" blurb="Archive X does not bundle model weights. The model stays in the user data directory or another configured local path." />
        <div className="grid grid-cols-2 gap-2">{readiness(status).map(([k, v]) => <div key={k} className="rounded-xl bg-bg p-3 text-left"><span className="block text-xs text-muted">{k}</span><b className="text-sm">{v}</b></div>)}</div>
        <p className="mt-3 text-xs text-muted">The recommended Q4_K_M model is roughly 2.5 GB. CPU-first inference works on most machines; GPU layers are optional.</p>
      </div>
      </div>
    </div>
  )
}
