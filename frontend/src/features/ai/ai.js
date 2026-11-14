/** Pure logic for the AI Assistance page (local llama.cpp assistant). Prompts and wording mirror the classic page. */
export const TABS = [
    { value: 'assistant', label: 'Assistant', blurb: 'Ask Archive X' }, { value: 'library', label: 'Library Intelligence', blurb: 'Search, metadata, duplicates' },
    { value: 'diagnostics', label: 'Diagnostics', blurb: 'Explain current health' }, { value: 'jobs', label: 'Jobs', blurb: 'Explain active work' },
    { value: 'recommendations', label: 'Recommendations', blurb: 'Evidence-based suggestions' }, { value: 'models', label: 'Models', blurb: 'Local runtime and model' },
];
export const EMPTY_HISTORY = 'Ask about your library, jobs, diagnostics, metadata, playback or the current Archive X state.';
export const DEFAULT_MODEL = 'Qwen3-4B-Instruct-2507-Q4_K_M';
export const DEFAULT_ENDPOINT = 'http://127.0.0.1:8080/v1';
export const TOOLS_ON = 'Read-only Archive X tools enabled.';
export const TOOLS_OFF = 'Tool registry unavailable.';
export const TOOLS_WAIT = 'Read-only tools available when the model is ready.';
export const CHAT_FAILED = 'Local AI request failed.';
export const NEED_QUERY = 'Enter a library question.';
export const QUICK = [
    { icon: 'picture', title: 'Find metadata problems', sub: 'Unidentified, partial or repair-needed videos', prompt: 'Find the most useful unresolved metadata problems in the current library and explain what needs attention.' },
    { icon: 'grid', title: 'Find duplicates', sub: 'Compare local OSHASH groups', prompt: 'Find duplicate video fingerprints and summarize the largest duplicate groups.' },
    { icon: 'alert', title: 'Explain diagnostics', sub: 'Use the bounded diagnostics tool', prompt: 'Explain the current diagnostics health and identify the highest-value issue to investigate next.' },
    { icon: 'transfer', title: 'Analyze jobs', sub: 'Explain current work and blockers', prompt: 'Summarize active and recent jobs, including anything waiting, failed or blocked.' },
];
export const LIBRARY_QUICK = [
    { title: 'Unresolved metadata', sub: 'Identify review candidates', prompt: 'Find unresolved metadata in the library and group the results by the reason they need review.' },
    { title: 'Duplicate review', sub: 'Fingerprint groups', prompt: 'Find duplicate OSHASH groups and explain which groups are most worth reviewing.' },
];
export const libraryPrompt = (q) => `Search the published Archive X library for: ${q}. Return the most useful matching videos, performers, studios or tags and explain the result.`;
export const DIAGNOSTICS_PROMPT = 'Use the diagnostics tool to summarize current Archive X health, identify the highest-value issue, and explain the next verification step. Do not expose raw secrets or unrelated log dumps.';
export const JOBS_PROMPT = 'Use the jobs tool to summarize active and recent Archive X jobs, their states, dependencies or blockers, and what should be checked next.';
export const RECOMMEND_PROMPT = 'Based only on current local Archive X state, give up to five useful recommendations for library quality, metadata review, diagnostics, or operational cleanup. Do not make changes and do not recommend overwriting protected manual overrides.';
export const chatBody = (message, page) => ({ message, page, selected: null });
export function header(s) {
    if (!s)
        return { ready: false, state: 'Checking local model…', model: '—' };
    const ready = Boolean(s.configured);
    return { ready, state: ready ? 'Local model ready' : s.runtime?.model?.exists ? 'Runtime configured — server not ready' : 'Local model not configured', model: `${s.provider || 'llama.cpp'} · ${s.model || '—'}` };
}
export const readiness = (s) => {
    const r = s?.runtime ?? {};
    return [['llama-server', r.runtime?.exists ? 'Ready' : 'Missing'], ['GGUF model', r.model?.exists ? 'Ready' : 'Missing'], ['Local endpoint', r.server?.reachable ? 'Reachable' : 'Not running'], ['Inference', r.ready ? 'Ready' : 'Not ready'], ['Mode', 'CPU-first'], ['Cloud API', 'Disabled']];
};
export const configFrom = (s) => ({ runtime_path: s?.runtime?.runtime?.path || '', model_path: s?.runtime?.model?.path || '', model_id: s?.model || DEFAULT_MODEL, endpoint: s?.runtime?.server?.endpoint || DEFAULT_ENDPOINT, context: Number(s?.runtime?.server?.context || 4096), gpu_layers: Number(s?.runtime?.server?.gpu_layers || 0) });
/** Request body: trimmed text, numbers clamped to the ranges the form allows. */
export const configBody = (c) => ({
    runtime_path: c.runtime_path.trim(), model_path: c.model_path.trim(), model_id: c.model_id.trim(), endpoint: c.endpoint.trim(),
    context: Math.min(16384, Math.max(1024, Math.round(Number(c.context) || 4096))), gpu_layers: Math.min(128, Math.max(0, Math.round(Number(c.gpu_layers) || 0))),
});
export const SAVED = 'Local AI settings saved.';
export const SAVE_FAILED = 'Could not save local AI settings.';
export const STARTING = 'Starting llama-server…';
export const STARTED = 'Local model server is ready.';
export const START_FAILED = 'Could not start local model.';
export const STOPPED = 'Local model server stopped.';
export const STOP_FAILED = 'Could not stop local model.';
export const TESTING = 'Running local inference test…';
export const TEST_FAILED = 'Local model test failed.';
export const testPassed = (ms, text) => `Model test passed in ${ms} ms: ${text}`;
export const messageTime = (m) => `${m.role === 'user' ? 'You' : 'Archive X Local AI'} · ${m.created_at || ''}`;
export const replyMeta = (model) => `Archive X Local AI · ${model || ''}`;
export const STATE_LABEL = { ready: 'Ready', stopped: 'Stopped', not_set_up: 'Not set up', not_downloaded: 'Not downloaded', needs_package: 'Needs install', downloading: 'Downloading…' };
export const stateTone = (s) => (s === 'ready' ? 'good' : s === 'downloading' ? 'busy' : 'warn');
export const sizeText = (mb) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);
export const modelsSummary = (r) => (r ? `${r.ready} of ${r.total} local models ready` : 'Checking local models…');
export const anyBusy = (r) => !!r?.models.some((m) => m.busy || m.state === 'downloading');
export const testLine = (r) => (r.ok ? `Working${r.ms != null ? ` · ${r.ms} ms` : ''}. ${r.detail ?? ''}`.trim() : `Not working: ${r.detail || 'the test failed.'}`);
