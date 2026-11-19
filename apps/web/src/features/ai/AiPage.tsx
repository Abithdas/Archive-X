import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { Disclosure } from '@/design/Accordion'
import { Icon } from '@/design/Icon'
import { ChatDot } from '@/design/ChatDot'
import { PageHead } from '@/design/PageHead'
import { Shimmer } from '@/design/Shimmer'
import { StreamText } from '@/design/StreamText'
import { ThinkingLine } from '@/design/ThinkingLine'
import { cn } from '@/design/cn'
import { AssistantSetup } from './AssistantSetup'
import { Findings } from './Findings'
import { Issues } from './Issues'
import { ModelCatalog } from './ModelCatalog'
import { StepsFeed } from './StepsFeed'
import { EMPTY_HISTORY, EXPLAIN_ISSUES_PROMPT, QUICK, SAVED, SAVE_FAILED, START_FAILED, STARTED, STARTING, STOPPED, STOP_FAILED, TABS, TEST_FAILED, TESTING, TOOLS_OFF, TOOLS_ON, TOOLS_WAIT, configBody, configFrom, header, isInstalled, messageTime, readiness, testPassed, type AiStatus, type AiTab, type Config, type Message } from './ai'
import { useAssistant, type LiveTurn } from './useAssistant'

const CARD = 'rounded-2xl border border-line bg-surface p-4'
const BTN = 'inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)

function Head({ title, blurb, children }: { title: string; blurb: string; children?: React.ReactNode }) {
  return <div className="mb-3 flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h2 className="text-lg font-bold">{title}</h2><p className="text-xs text-muted">{blurb}</p></div>{children}</div>
}
const Empty = ({ children }: { children: React.ReactNode }) => <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">{children}</p>

function Bubble({ role, meta, children }: { role: 'user' | 'assistant'; meta: string; children: React.ReactNode }) {
  return (
    <div className={cn('rounded-xl p-3 text-left text-sm', role === 'user' ? 'ml-6 bg-accent/10' : 'mr-6 bg-surface-2')}>
      <div className="mb-1 text-[11px] text-muted">{meta}</div>
      <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">{children}</div>
    </div>
  )
}

/** The model's reasoning as it is written (only when "Show the model's reasoning" is on): a quiet block that shimmers its title while the model is still thinking. */
function Reasoning({ text, active }: { text: string; active: boolean }) {
  return (
    <Disclosure defaultOpen className="rounded-lg bg-bg/60 p-2.5" summaryClassName="text-xs font-semibold text-muted" summary={active ? <Shimmer text="Reasoning…" /> : 'Reasoning'}>
      <div className="mt-2 max-h-40 overflow-y-auto text-xs text-muted [overflow-wrap:anywhere]"><StreamText text={text} /></div>
    </Disclosure>
  )
}

/** What the assistant is doing while it works: a breathing dot beside a shimmering line that changes with the real step, the tools it used, its
 *  reasoning if it shows any, then the answer arriving word by word with the dot still breathing after the last word until it is finished. */
function TurnBody({ turn }: { turn: LiveTurn }) {
  const working = !turn.done && !turn.error
  return (
    <div className="grid gap-2">
      {turn.steps.length ? <StepsFeed label="Steps the assistant took" className="rounded-lg bg-bg/60 p-2.5" steps={turn.steps.map((s, i) => ({ id: `${i}`, label: s.label, state: s.state }))} /> : null}
      {turn.reasoning ? <Reasoning text={turn.reasoning} active={working && !turn.text} /> : null}
      {working && !turn.text ? <div className="flex items-center gap-2.5 text-muted"><ChatDot label="Working" className="text-sm" /><ThinkingLine text={turn.status || 'Thinking…'} /></div> : null}
      {turn.text ? <StreamText text={turn.text} /> : null}
      {working && turn.text ? <div><ChatDot className="text-sm" /></div> : null}
      {turn.error ? <span role="alert" className="text-bad">{turn.error}</span> : null}
    </div>
  )
}

function LiveTurnView({ turn }: { turn: LiveTurn }) {
  return (
    <>
      <Bubble role="user" meta="You · just now">{turn.user}</Bubble>
      <Bubble role="assistant" meta={`Archive X Local AI${turn.meta ? ` · ${turn.meta}` : turn.done ? '' : ' · working…'}`}><TurnBody turn={turn} /></Bubble>
    </>
  )
}

/** Local, private AI assistant: set up in one step, the issue list, chat, the notebook and the local models. */
export function AiPage({ initialTab = 'issues' }: { initialTab?: AiTab } = {}) {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['ai-status'] as const, queryFn: () => api<AiStatus>('/api/ai/status'), staleTime: 0, retry: false, refetchInterval: (q) => (q.state.data?.runtime?.server?.starting ? 2000 : false) })
  const history = useQuery({ queryKey: ['ai-history'] as const, queryFn: () => api<{ messages?: Message[] }>('/api/ai/history'), staleTime: Infinity, retry: false })
  const tools = useQuery({ queryKey: ['ai-tools'] as const, queryFn: () => api<{ local_only?: boolean }>('/api/ai/tools'), staleTime: 60_000, retry: false })
  const [tab, setTab] = useState<AiTab>(initialTab)
  const { rows, turn, ask, busy } = useAssistant(history.data?.messages)
  const [input, setInput] = useState('')
  const log = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = log.current; if (el) el.scrollTop = el.scrollHeight }, [rows, turn?.text, turn?.steps.length, turn?.status])
  const refreshed = () => { void qc.invalidateQueries({ queryKey: ['ai-status'] }); void qc.invalidateQueries({ queryKey: ['ai-models'] }) }
  // Asking starts the model when it is asleep; the header and the model list catch up afterwards.
  const send = async (text: string) => { const out = await ask(text); refreshed(); return out }
  const quick = (p: string) => { setTab('assistant'); void send(p) }
  const open = (t: AiTab) => { setTab(t); if (t === 'models') void status.refetch(); }
  const h = header(status.isError ? { configured: false } : status.data)
  const hdr = status.isError ? { ...h, state: 'AI status unavailable', model: 'Local AI service unavailable' } : h
  const starting = Boolean(status.data?.runtime?.server?.starting) || Boolean(busy && turn?.status.startsWith('Starting'))
  const needsSetup = Boolean(status.data) && !isInstalled(status.data) && tab !== 'models' && tab !== 'findings' && tab !== 'issues'
  const finishedSetup = () => { refreshed(); void history.refetch() }

  return (
    <section className="w-full px-4 pb-10 pt-6 sm:px-10">
      <PageHead eyebrow="Local intelligence" title="AI Assistance" blurb="Find what is going wrong, and ask a private local model about it. It diagnoses; it never changes anything.">
        <div className="flex items-center gap-2.5 rounded-xl border border-line px-3 py-2" role="status" aria-live="polite">
          {starting ? <ChatDot label="Starting" className="text-sm" /> : <span aria-hidden="true" className={cn('size-2.5 rounded-full', hdr.ready ? 'bg-good' : 'bg-warn')} />}
          <div className="text-left"><strong className="block text-sm">{starting ? <Shimmer text={hdr.state} /> : hdr.state}</strong><small className="text-xs text-muted">{hdr.model}</small></div>
        </div>
      </PageHead>
      <div className="grid items-start gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="AI Assistance sections" className="flex gap-1.5 overflow-x-auto md:sticky md:top-20 md:grid md:content-start md:self-start">
          {TABS.map((t) => (
            <button key={t.value} type="button" aria-current={tab === t.value ? 'page' : undefined} onClick={() => open(t.value)} className={cn('shrink-0 rounded-xl border px-3 py-2 text-left', tab === t.value ? 'border-accent bg-accent/10' : 'border-line hover:bg-fg/5')}>
              <b className="block text-sm">{t.label}</b><small className="text-[11px] text-muted">{t.blurb}</small>
            </button>
          ))}
        </nav>
        <main className="min-w-0">
          {needsSetup ? <AssistantSetup onFinished={finishedSetup} /> : null}
          {!needsSetup && tab === 'assistant' ? (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
              <div className={CARD}>
                <Head title="Archive X Assistant" blurb="Uses only local model inference and controlled Archive X read-only tools."><span className="rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good">LOCAL ONLY</span></Head>
                <div ref={log} role="log" aria-label="Assistant conversation" className="mb-3 grid max-h-[50vh] min-h-40 content-start gap-2 overflow-y-auto">
                  {!rows.length && !turn ? <Empty>{history.isPending ? 'Loading the conversation…' : EMPTY_HISTORY}</Empty> : null}
                  {rows.map((m, i) => (
                    <Bubble key={i} role={m.role === 'user' ? 'user' : 'assistant'} meta={m.role === 'user' ? messageTime(m) : `Archive X Local AI${m.created_at ? ` · ${m.created_at}` : ''}`}><StreamText text={m.content ?? ''} instant /></Bubble>
                  ))}
                  {turn ? <LiveTurnView turn={turn} /> : null}
                </div>
                <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={3} placeholder="Ask Archive X anything…" aria-label="Message" onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!busy && input.trim()) { const t = input; setInput(''); void send(t) } } }} className="w-full resize-y rounded-xl border border-line-2 bg-bg p-3 text-sm" />
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted">{tools.data ? (tools.data.local_only ? TOOLS_ON : TOOLS_OFF) : tools.isError ? TOOLS_OFF : TOOLS_WAIT}</span>
                  <button type="button" className={PRIMARY} disabled={busy || !input.trim()} onClick={() => { const t = input; setInput(''); void send(t) }}>Ask</button></div>
              </div>
              <div className={CARD}>
                <Head title="Quick actions" blurb="Bounded questions that use Archive X's local data." />
                <div className="grid gap-2">{QUICK.map((q) => (
                  <button key={q.title} type="button" disabled={busy} onClick={() => quick(q.prompt)} className="flex items-center gap-3 rounded-xl border border-line p-3 text-left hover:bg-fg/5 disabled:opacity-60">
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-accent"><Icon name={q.icon} className="size-4" /></span>
                    <span className="min-w-0 flex-1"><b className="block text-sm">{q.title}</b><small className="text-xs text-muted">{q.sub}</small></span><span aria-hidden="true" className="text-muted">›</span>
                  </button>))}</div>
              </div>
            </div>
          ) : null}
          {tab === 'issues' ? <Issues onExplain={isInstalled(status.data) ? () => quick(EXPLAIN_ISSUES_PROMPT) : undefined} /> : null}
          {tab === 'findings' ? <Findings /> : null}
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
          <Head title="Assistant engine" blurb="Archive X starts it by itself when you ask a question, and stops it after 20 idle minutes. These buttons are for checking it."><span className="rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good">NO CLOUD API</span></Head>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={PRIMARY} disabled={busy} onClick={() => void run('/api/ai/runtime/start', {}, STARTING, () => STARTED, START_FAILED)}>Start local model</button>
            <button type="button" className={BTN} disabled={busy} onClick={() => void run('/api/ai/runtime/stop', {}, 'Stopping…', () => STOPPED, STOP_FAILED)}>Stop</button>
            <button type="button" className={BTN} disabled={busy} onClick={() => void run('/api/ai/test', {}, TESTING, (r) => testPassed(r.latency_ms, r.text), TEST_FAILED)}>Test model</button>
          </div>
          <p role="status" aria-live="polite" className="mt-2 min-h-5 text-sm text-muted">{busy ? <span className="inline-flex items-center gap-2"><ChatDot label="Working" className="text-sm" /><Shimmer text={msg} /></span> : msg}</p>
          <Disclosure className="mt-3 border-t border-line pt-3" summaryClassName="text-sm font-bold" summary="Advanced: use your own engine and model file">
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">{field('llama-server executable', 'runtime_path', 'Path to llama-server.exe')}</div>
              <div className="sm:col-span-2">{field('GGUF model file', 'model_path', 'Path to a .gguf model')}</div>
              {field('Model id', 'model_id')}{field('Local endpoint', 'endpoint')}{field('Context tokens', 'context', '', [1024, 16384])}{field('GPU layers', 'gpu_layers', '', [0, 128])}
            </div>
            <label className="mt-3 flex items-center gap-2 text-left text-sm"><input type="checkbox" checked={cfg.thinking} onChange={(e) => set('thinking', e.target.checked)} className="size-4 accent-[var(--color-accent)]" />Show the model's reasoning while it answers (slower)</label>
            <div className="mt-3"><button type="button" className={BTN} disabled={busy} onClick={() => void run('/api/ai/config', configBody(cfg), 'Saving…', () => SAVED, SAVE_FAILED)}>Save</button></div>
          </Disclosure>
        </div>
        <div className={CARD}>
          <Head title="Readiness" blurb="Archive X does not bundle model weights. They are downloaded once, checked, and kept in your data folder." />
          <div className="grid grid-cols-2 gap-2">{readiness(status).map(([k, v]) => <div key={k} className="rounded-xl bg-bg p-3 text-left"><span className="block text-xs text-muted">{k}</span><b className="text-sm">{v}</b></div>)}</div>
          <p className="mt-3 text-xs text-muted">The Balanced model is about 2.4 GB. A processor is enough; a graphics card makes answers quicker.</p>
        </div>
      </div>
    </div>
  )
}
