/** Pure logic for the AI Assistance page (local llama.cpp assistant). Prompts and wording mirror the classic page. */

export type AiTab = 'issues' | 'assistant' | 'findings' | 'models'
export const TABS: Array<{ value: AiTab; label: string; blurb: string }> = [
  { value: 'issues', label: 'Issues', blurb: 'What is going wrong, no model needed' }, { value: 'assistant', label: 'Assistant', blurb: 'Ask about an issue' },
  { value: 'findings', label: 'Findings', blurb: 'What it noticed' }, { value: 'models', label: 'Models', blurb: 'Local runtime and model' },
]

export interface Message { role: string; content?: string; created_at?: string }
interface Runtime {
  runtime?: { path?: string; exists?: boolean }; model?: { path?: string; exists?: boolean }
  server?: { endpoint?: string; reachable?: boolean; starting?: boolean; context?: number; gpu_layers?: number; thinking?: boolean }; ready?: boolean
}
export interface AiStatus { configured?: boolean; provider?: string; model?: string; runtime?: Runtime }

export const EMPTY_HISTORY = 'Ask about your library, jobs, diagnostics, metadata, playback or the current Archive X state.'
export const DEFAULT_MODEL = 'Qwen3-4B-Instruct-2507-Q4_K_M'
export const DEFAULT_ENDPOINT = 'http://127.0.0.1:8080/v1'
export const TOOLS_ON = 'Read-only Archive X tools enabled.'
export const TOOLS_OFF = 'Tool registry unavailable.'
export const TOOLS_WAIT = 'Read-only tools available when the model is ready.'
export const CHAT_FAILED = 'Local AI request failed.'

export const QUICK = [
  { icon: 'picture', title: 'Find metadata problems', sub: 'Unidentified, partial or repair-needed videos', prompt: 'Find the most useful unresolved metadata problems in the current library and explain what needs attention.' },
  { icon: 'grid', title: 'Find duplicates', sub: 'Compare local OSHASH groups', prompt: 'Find duplicate video fingerprints and summarize the largest duplicate groups.' },
  { icon: 'alert', title: 'Explain diagnostics', sub: 'Use the bounded diagnostics tool', prompt: 'Explain the current diagnostics health and identify the highest-value issue to investigate next.' },
  { icon: 'transfer', title: 'Analyze jobs', sub: 'Explain current work and blockers', prompt: 'Summarize active and recent jobs, including anything waiting, failed or blocked.' },
] as const
export const EXPLAIN_ISSUES_PROMPT = 'Call get_issue_digest and explain each issue in it in two short sentences, worst first: what is happening and what to check next. Diagnose only; do not write code or changes.'
export const chatBody = (message: string, page: string) => ({ message, page, selected: null })

export interface Header { ready: boolean; state: string; model: string }
export function header(s: AiStatus | null | undefined): Header {
  if (!s) return { ready: false, state: 'Checking local model…', model: '—' }
  const r = s.runtime
  const files = Boolean(r?.runtime?.exists && r?.model?.exists)
  const up = Boolean(r?.server?.reachable)
  const state = up ? 'Assistant ready' : r?.server?.starting ? 'Starting the assistant…' : files ? 'Asleep — starts when you ask' : 'Not set up yet'
  return { ready: up, state, model: `${s.provider || 'llama.cpp'} · ${s.model || '—'}` }
}
/** True when the engine and the model are both on this PC (whether or not the server is running). */
export const isInstalled = (s: AiStatus | null | undefined) => Boolean(s?.runtime?.runtime?.exists && s?.runtime?.model?.exists)
export const readiness = (s: AiStatus | null | undefined): Array<[string, string]> => {
  const r = s?.runtime ?? {}
  return [['llama-server', r.runtime?.exists ? 'Ready' : 'Missing'], ['GGUF model', r.model?.exists ? 'Ready' : 'Missing'], ['Local endpoint', r.server?.reachable ? 'Reachable' : 'Not running'], ['Inference', r.ready ? 'Ready' : 'Not ready'], ['Mode', 'CPU-first'], ['Cloud API', 'Disabled']]
}

export interface Config { runtime_path: string; model_path: string; model_id: string; endpoint: string; context: number; gpu_layers: number; thinking: boolean }
export const configFrom = (s: AiStatus | null | undefined): Config => ({ runtime_path: s?.runtime?.runtime?.path || '', model_path: s?.runtime?.model?.path || '', model_id: s?.model || DEFAULT_MODEL, endpoint: s?.runtime?.server?.endpoint || DEFAULT_ENDPOINT, context: Number(s?.runtime?.server?.context || 8192), gpu_layers: Number(s?.runtime?.server?.gpu_layers || 0), thinking: Boolean(s?.runtime?.server?.thinking) })
/** Request body: trimmed text, numbers clamped to the ranges the form allows. */
export const configBody = (c: Config): Config => ({
  runtime_path: c.runtime_path.trim(), model_path: c.model_path.trim(), model_id: c.model_id.trim(), endpoint: c.endpoint.trim(),
  context: Math.min(16384, Math.max(1024, Math.round(Number(c.context) || 8192))), gpu_layers: Math.min(128, Math.max(0, Math.round(Number(c.gpu_layers) || 0))), thinking: Boolean(c.thinking),
})
export const SAVED = 'Local AI settings saved.'
export const SAVE_FAILED = 'Could not save local AI settings.'
export const STARTING = 'Starting llama-server…'
export const STARTED = 'Local model server is ready.'
export const START_FAILED = 'Could not start local model.'
export const STOPPED = 'Local model server stopped.'
export const STOP_FAILED = 'Could not stop local model.'
export const TESTING = 'Running local inference test…'
export const TEST_FAILED = 'Local model test failed.'
export const testPassed = (ms: unknown, text: unknown) => `Model test passed in ${ms} ms: ${text}`
export const messageTime = (m: Message) => `${m.role === 'user' ? 'You' : 'Archive X Local AI'} · ${m.created_at || ''}`
export const replyMeta = (model: string | undefined) => `Archive X Local AI · ${model || ''}`

export type ModelState = 'ready' | 'stopped' | 'not_set_up' | 'not_downloaded' | 'needs_package' | 'downloading'
export interface LocalModel { id: string; name: string; detail: string; size_mb: number; licence: string; state: ModelState; installable: boolean; powers: string[]; hint: string; busy?: boolean; error?: string }
export interface ModelsReply { ok?: boolean; ready: number; total: number; models: LocalModel[] }
export const STATE_LABEL: Record<ModelState, string> = { ready: 'Ready', stopped: 'Stopped', not_set_up: 'Not set up', not_downloaded: 'Not downloaded', needs_package: 'Needs install', downloading: 'Downloading…' }
export const stateTone = (s: ModelState): 'good' | 'warn' | 'busy' => (s === 'ready' ? 'good' : s === 'downloading' ? 'busy' : 'warn')
export const sizeText = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`)
export const modelsSummary = (r?: Pick<ModelsReply, 'ready' | 'total'>) => (r ? `${r.ready} of ${r.total} local models ready` : 'Checking local models…')
export const anyBusy = (r?: ModelsReply) => !!r?.models.some((m) => m.busy || m.state === 'downloading')
export const testLine = (r: { ok?: boolean; ms?: number | null; detail?: string }) => (r.ok ? `Working${r.ms != null ? ` · ${r.ms} ms` : ''}. ${r.detail ?? ''}`.trim() : `Not working: ${r.detail || 'the test failed.'}`)


export interface SetupOption { id: 'small' | 'balanced' | 'strong'; label: string; model: string; size_mb: number; min_ram_gb: number; blurb: string; recommended: boolean; fits: boolean }
export interface SetupEngine { id: string; label: string; size_mb: number; gpu: boolean }
export interface SetupStep { id: string; label: string; state: 'pending' | 'active' | 'done' | 'error'; detail: string }
export interface SetupRun { state: 'idle' | 'running' | 'done' | 'error' | 'cancelled'; model: string; engine: string; steps: SetupStep[]; bytes_done: number; bytes_total: number; speed_bps: number; log: string[]; error: string }
export interface SetupInfo { ok?: boolean; supported: boolean; ram_gb: number; recommended: SetupOption['id']; models: SetupOption[]; engines: SetupEngine[]; engine_tag: string; run: SetupRun }
export const percentOf = (done: number, total: number) => (total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0)
export const bytesText = (n: number) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`)
export const speedText = (bps: number) => (bps > 0 ? `${bytesText(bps)}/s` : '')
export const setupTotal = (m: SetupOption | undefined, e: SetupEngine | undefined) => `${(((m?.size_mb ?? 0) + (e?.size_mb ?? 0)) / 1024).toFixed(1)} GB`
/** A PC sold as 16 GB reports about 15.7, so the line is drawn a little below the number. */
export const RAM_SLACK = 0.9
export const ramNote = (ram: number, o: SetupOption) => (ram && ram < o.min_ram_gb * RAM_SLACK ? `Wants ${o.min_ram_gb} GB of memory; this PC has ${ram} GB.` : '')
