import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Icon } from '@/design/Icon';
import { PageHead } from '@/design/PageHead';
import { cn } from '@/design/cn';
import { ModelCatalog } from './ModelCatalog';
import { CHAT_FAILED, DIAGNOSTICS_PROMPT, EMPTY_HISTORY, JOBS_PROMPT, LIBRARY_QUICK, NEED_QUERY, QUICK, RECOMMEND_PROMPT, SAVED, SAVE_FAILED, START_FAILED, STARTED, STARTING, STOPPED, STOP_FAILED, TABS, TEST_FAILED, TESTING, TOOLS_OFF, TOOLS_ON, TOOLS_WAIT, chatBody, configBody, configFrom, header, libraryPrompt, messageTime, readiness, replyMeta, testPassed } from './ai';
const CARD = 'rounded-2xl border border-line bg-surface p-4';
const BTN = 'inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50';
const reason = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb);
function Head({ title, blurb, children }) {
    return _jsxs("div", { className: "mb-3 flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("h2", { className: "text-lg font-bold", children: title }), _jsx("p", { className: "text-xs text-muted", children: blurb })] }), children] });
}
const Empty = ({ children }) => _jsx("p", { className: "rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted", children: children });
/** Local, private AI assistant: chat, library/diagnostic/job/recommendation analyses, and local model setup. */
export function AiPage() {
    const qc = useQueryClient();
    const status = useQuery({ queryKey: ['ai-status'], queryFn: () => api('/api/ai/status'), staleTime: 0, retry: false });
    const history = useQuery({ queryKey: ['ai-history'], queryFn: () => api('/api/ai/history'), staleTime: Infinity, retry: false });
    const tools = useQuery({ queryKey: ['ai-tools'], queryFn: () => api('/api/ai/tools'), staleTime: 60_000, retry: false });
    const [tab, setTab] = useState('assistant');
    const [rows, setRows] = useState([]);
    const seeded = useRef(false);
    const [input, setInput] = useState('');
    const [asking, setAsking] = useState(false);
    const [libQ, setLibQ] = useState('');
    const [results, setResults] = useState({});
    const [working, setWorking] = useState({});
    const log = useRef(null);
    useEffect(() => { if (!seeded.current && history.data) {
        seeded.current = true;
        setRows(history.data.messages ?? []);
    } }, [history.data]);
    useEffect(() => { const el = log.current; if (el)
        el.scrollTop = el.scrollHeight; }, [rows]);
    /** Sends one chat message; the exchange is shown in the Assistant history whichever tab asked. */
    const ask = useCallback(async (message) => {
        const text = message.trim();
        if (!text)
            return null;
        setRows((r) => [...r, { role: 'user', content: text, created_at: 'just now' }, { role: 'assistant', content: 'Analyzing local Archive X data…', created_at: 'working…', pending: true }]);
        try {
            const r = await api('/api/ai/chat', { method: 'POST', json: chatBody(text, location.pathname + location.search) });
            if (!r || r.detail)
                throw new ApiError(0, r?.detail || CHAT_FAILED);
            setRows((cur) => [...cur.filter((m) => !m.pending), { role: 'assistant', content: r.text ?? '', created_at: replyMeta(r.model).replace('Archive X Local AI · ', '') }]);
            return r.text ?? '';
        }
        catch (e) {
            setRows((cur) => [...cur.filter((m) => !m.pending), { role: 'assistant', content: reason(e, CHAT_FAILED), created_at: '' }]);
            return null;
        }
    }, []);
    const send = async (text) => { setAsking(true); try {
        await ask(text);
    }
    finally {
        setAsking(false);
    } };
    const analyse = useCallback(async (t, prompt, empty) => {
        setWorking((w) => ({ ...w, [t]: true }));
        const out = await ask(prompt);
        setResults((r) => ({ ...r, [t]: out || empty }));
        setWorking((w) => ({ ...w, [t]: false }));
    }, [ask]);
    const diag = () => analyse('diagnostics', DIAGNOSTICS_PROMPT, 'No diagnostics analysis available.');
    const jobs = () => analyse('jobs', JOBS_PROMPT, 'No job analysis available.');
    const quick = (p) => { setTab('assistant'); void send(p); };
    const open = (t) => { setTab(t); if (t === 'models')
        void status.refetch(); if (t === 'diagnostics')
        void diag(); if (t === 'jobs')
        void jobs(); };
    const librarySearch = async () => {
        const q = libQ.trim();
        if (!q)
            return void toast(NEED_QUERY);
        setWorking((w) => ({ ...w, library: true }));
        const out = await ask(libraryPrompt(q));
        setResults((r) => ({ ...r, library: out || 'No result' }));
        setWorking((w) => ({ ...w, library: false }));
    };
    const h = header(status.isError ? { configured: false } : status.data);
    const hdr = status.isError ? { ...h, state: 'AI status unavailable', model: 'Local AI service unavailable' } : h;
    return (_jsxs("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(PageHead, { eyebrow: "Local intelligence", title: "AI Assistance", blurb: "Private, local AI for Archive X library intelligence, diagnostics, jobs and recommendations.", children: _jsxs("div", { className: "flex items-center gap-2.5 rounded-xl border border-line px-3 py-2", role: "status", "aria-live": "polite", children: [_jsx("span", { "aria-hidden": "true", className: cn('size-2.5 rounded-full', hdr.ready ? 'bg-good' : 'bg-warn') }), _jsxs("div", { className: "text-left", children: [_jsx("strong", { className: "block text-sm", children: hdr.state }), _jsx("small", { className: "text-xs text-muted", children: hdr.model })] })] }) }), _jsxs("div", { className: "grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]", children: [_jsx("nav", { "aria-label": "AI Assistance sections", className: "flex gap-1.5 overflow-x-auto md:grid md:content-start", children: TABS.map((t) => (_jsxs("button", { type: "button", "aria-current": tab === t.value ? 'page' : undefined, onClick: () => open(t.value), className: cn('shrink-0 rounded-xl border px-3 py-2 text-left', tab === t.value ? 'border-accent bg-accent/10' : 'border-transparent hover:bg-fg/5'), children: [_jsx("b", { className: "block text-sm", children: t.label }), _jsx("small", { className: "text-[11px] text-muted", children: t.blurb })] }, t.value))) }), _jsxs("main", { className: "min-w-0", children: [tab === 'assistant' ? (_jsxs("div", { className: "grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]", children: [_jsxs("div", { className: CARD, children: [_jsx(Head, { title: "Archive X Assistant", blurb: "Uses only local model inference and controlled Archive X read-only tools.", children: _jsx("span", { className: "rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good", children: "LOCAL ONLY" }) }), _jsx("div", { ref: log, role: "log", "aria-label": "Assistant conversation", className: "mb-3 grid max-h-[50vh] min-h-40 content-start gap-2 overflow-y-auto", children: !rows.length ? _jsx(Empty, { children: EMPTY_HISTORY }) : rows.map((m, i) => (_jsxs("div", { className: cn('rounded-xl p-3 text-left text-sm', m.role === 'user' ? 'ml-6 bg-accent/10' : 'mr-6 bg-surface-2'), children: [_jsx("div", { className: "mb-1 text-[11px] text-muted", children: m.role === 'user' ? messageTime(m) : `Archive X Local AI${m.created_at ? ` · ${m.created_at}` : ''}` }), _jsx("div", { className: "whitespace-pre-wrap [overflow-wrap:anywhere]", children: m.content })] }, i))) }), _jsx("textarea", { value: input, onChange: (e) => setInput(e.target.value), rows: 3, placeholder: "Ask Archive X anything\u2026", "aria-label": "Message", onKeyDown: (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                                                    e.preventDefault();
                                                    if (input.trim() && !asking) {
                                                        const t = input;
                                                        setInput('');
                                                        void send(t);
                                                    }
                                                } }, className: "w-full resize-y rounded-xl border border-line-2 bg-bg p-3 text-sm" }), _jsxs("div", { className: "mt-2 flex flex-wrap items-center justify-between gap-2", children: [_jsx("span", { className: "text-xs text-muted", children: tools.data ? (tools.data.local_only ? TOOLS_ON : TOOLS_OFF) : tools.isError ? TOOLS_OFF : TOOLS_WAIT }), _jsx("button", { type: "button", className: PRIMARY, disabled: asking || !input.trim(), onClick: () => { const t = input; setInput(''); void send(t); }, children: "Ask" })] })] }), _jsxs("div", { className: CARD, children: [_jsx(Head, { title: "Quick actions", blurb: "Bounded questions that use Archive X's local data." }), _jsx("div", { className: "grid gap-2", children: QUICK.map((q) => (_jsxs("button", { type: "button", disabled: asking, onClick: () => quick(q.prompt), className: "flex items-center gap-3 rounded-xl border border-line p-3 text-left hover:bg-fg/5 disabled:opacity-60", children: [_jsx("span", { className: "grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-accent", children: _jsx(Icon, { name: q.icon, className: "size-4" }) }), _jsxs("span", { className: "min-w-0 flex-1", children: [_jsx("b", { className: "block text-sm", children: q.title }), _jsx("small", { className: "text-xs text-muted", children: q.sub })] }), _jsx("span", { "aria-hidden": "true", className: "text-muted", children: "\u203A" })] }, q.title))) })] })] })) : null, tab === 'library' ? (_jsxs("div", { className: "grid gap-4", children: [_jsxs("div", { className: CARD, children: [_jsx(Head, { title: "Library Intelligence", blurb: "Ask the local model to inspect the published library without bypassing the publication boundary." }), _jsxs("div", { className: "mb-3 flex flex-wrap gap-2", children: [_jsx("input", { value: libQ, onChange: (e) => setLibQ(e.target.value), onKeyDown: (e) => e.key === 'Enter' && void librarySearch(), "aria-label": "Library question", placeholder: "e.g. find videos related to a performer, studio or title", className: "h-10 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3 text-sm" }), _jsx("button", { type: "button", className: PRIMARY, disabled: working.library, onClick: () => void librarySearch(), children: "Search with AI" })] }), _jsx("div", { className: "grid gap-2 sm:grid-cols-2", children: LIBRARY_QUICK.map((q) => _jsxs("button", { type: "button", disabled: working.library, onClick: () => { setWorking((w) => ({ ...w, library: true })); void ask(q.prompt).then((out) => { setResults((r) => ({ ...r, library: out || 'No result' })); setWorking((w) => ({ ...w, library: false })); }); }, className: "rounded-xl border border-line p-3 text-left hover:bg-fg/5 disabled:opacity-60", children: [_jsx("b", { className: "block text-sm", children: q.title }), _jsx("small", { className: "text-xs text-muted", children: q.sub })] }, q.title)) })] }), _jsx("div", { className: CARD, "aria-live": "polite", children: working.library ? _jsx("p", { className: "text-sm text-muted", children: "Analyzing local Archive X data\u2026" }) : results.library ? _jsx("p", { className: "whitespace-pre-wrap text-left text-sm [overflow-wrap:anywhere]", children: results.library }) : _jsx(Empty, { children: "Run a library intelligence action to see the result here." }) })] })) : null, tab === 'diagnostics' || tab === 'jobs' || tab === 'recommendations' ? (() => {
                                const meta = { diagnostics: ['Diagnostics', 'Explain current operational evidence without exposing raw logs to the model by default.', 'Refresh analysis', diag, 'Analyzing current diagnostics…'], jobs: ['Jobs', 'Understand active work, dependencies and blockers using the existing JobManager state.', 'Refresh analysis', jobs, 'Analyzing jobs…'], recommendations: ['Recommendations', 'Recommendations are advisory; protected metadata overrides and mutations remain operator-controlled.', 'Generate', () => analyse('recommendations', RECOMMEND_PROMPT, 'No recommendations available.'), 'Generating recommendations…'] };
                                const [title, blurb, label, run, busy] = meta[tab];
                                return (_jsxs("div", { className: CARD, children: [_jsx(Head, { title: title, blurb: blurb, children: _jsx("button", { type: "button", className: BTN, disabled: working[tab], onClick: () => void run(), children: label }) }), _jsx("div", { "aria-live": "polite", className: "whitespace-pre-wrap text-left text-sm [overflow-wrap:anywhere]", children: working[tab] ? busy : results[tab] ?? 'No analysis requested.' })] }));
                            })() : null, tab === 'models' ? _jsx(Models, { status: status.data, onChanged: () => qc.invalidateQueries({ queryKey: ['ai-status'] }) }) : null] })] })] }));
}
function Models({ status, onChanged }) {
    const [cfg, setCfg] = useState(() => configFrom(status));
    const [msg, setMsg] = useState('');
    const [busy, setBusy] = useState(false);
    const filled = useRef(Boolean(status));
    useEffect(() => { if (status && !filled.current) {
        filled.current = true;
        setCfg(configFrom(status));
    } }, [status]);
    const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }));
    const run = async (path, body, pending, ok, fail) => {
        setBusy(true);
        setMsg(pending);
        try {
            const r = await api(path, { method: 'POST', json: body });
            setMsg(r && r.ok ? ok(r) : String(r?.detail || fail));
        }
        catch (e) {
            setMsg(reason(e, fail));
        }
        finally {
            setBusy(false);
            await onChanged();
        }
    };
    const field = (label, k, ph = '', num) => (_jsxs("label", { className: "grid gap-1 text-left text-xs font-semibold text-muted", children: [label, _jsx("input", { value: String(cfg[k]), onChange: (e) => set(k, (num ? Number(e.target.value) : e.target.value)), type: num ? 'number' : 'text', min: num?.[0], max: num?.[1], placeholder: ph, className: "h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm font-normal text-fg" })] }));
    return (_jsxs("div", { className: "grid gap-4", children: [_jsx(ModelCatalog, {}), _jsxs("div", { className: "grid gap-4 xl:grid-cols-2", children: [_jsxs("div", { className: CARD, children: [_jsx(Head, { title: "Local model", blurb: "Recommended baseline for this application: Qwen3 4B Instruct 2507, Q4_K_M.", children: _jsx("span", { className: "rounded-full bg-good/15 px-2.5 py-1 text-[11px] font-bold text-good", children: "NO CLOUD API" }) }), _jsxs("div", { className: "grid gap-3 sm:grid-cols-2", children: [_jsx("div", { className: "sm:col-span-2", children: field('llama-server executable', 'runtime_path', 'Path to llama-server.exe') }), _jsx("div", { className: "sm:col-span-2", children: field('GGUF model file', 'model_path', 'Path to Qwen3-4B-Instruct-2507-Q4_K_M.gguf') }), field('Model id', 'model_id'), field('Local endpoint', 'endpoint'), field('Context tokens', 'context', '', [1024, 16384]), field('GPU layers', 'gpu_layers', '', [0, 128])] }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => void run('/api/ai/config', configBody(cfg), 'Saving…', () => SAVED, SAVE_FAILED), children: "Save" }), _jsx("button", { type: "button", className: PRIMARY, disabled: busy, onClick: () => void run('/api/ai/runtime/start', {}, STARTING, () => STARTED, START_FAILED), children: "Start local model" }), _jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => void run('/api/ai/runtime/stop', {}, 'Stopping…', () => STOPPED, STOP_FAILED), children: "Stop" }), _jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => void run('/api/ai/test', {}, TESTING, (r) => testPassed(r.latency_ms, r.text), TEST_FAILED), children: "Test model" })] }), _jsx("p", { role: "status", "aria-live": "polite", className: "mt-2 text-sm text-muted", children: msg })] }), _jsxs("div", { className: CARD, children: [_jsx(Head, { title: "Readiness", blurb: "Archive X does not bundle model weights. The model stays in the user data directory or another configured local path." }), _jsx("div", { className: "grid grid-cols-2 gap-2", children: readiness(status).map(([k, v]) => _jsxs("div", { className: "rounded-xl bg-bg p-3 text-left", children: [_jsx("span", { className: "block text-xs text-muted", children: k }), _jsx("b", { className: "text-sm", children: v })] }, k)) }), _jsx("p", { className: "mt-3 text-xs text-muted", children: "The recommended Q4_K_M model is roughly 2.5 GB. CPU-first inference works on most machines; GPU layers are optional." })] })] })] }));
}
