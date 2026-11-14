import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { useAutomationConfig, useSourceChannels, useStatus } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { DEFAULT_FORM, automationState, configBody, formFromConfig, queueView, reviewWarnings, startProblem, stepProblem } from './automation';
import { CARD, Check, FIELD, Field, Stepper, detail } from './parts';
const STEPS = ['Source', 'Runtime & connectivity', 'Release rules', 'Queue & activity', 'Review', 'Run'];
function Saved({ on }) {
    return _jsxs("span", { className: cn('ml-2 inline-flex items-center gap-1 text-[11px] font-normal', on ? 'text-good' : 'text-faint'), children: [_jsx("i", { className: cn('size-1.5 rounded-full', on ? 'bg-good' : 'bg-surface-3') }), on ? 'Saved' : 'Not saved'] });
}
const Bar = ({ pct }) => _jsx("div", { className: "h-1.5 overflow-hidden rounded-full bg-surface-3", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(pct), children: _jsx("span", { className: cn('block h-full rounded-full transition-[width]', pct >= 100 ? 'bg-good' : 'bg-accent'), style: { width: `${pct}%` } }) });
function ActiveCard({ x, onSkip }) {
    return (_jsxs("li", { className: "rounded-xl border border-accent/40 bg-accent/5 p-3", children: [_jsx("p", { className: "truncate font-bold", title: x.title, children: x.title }), _jsxs("p", { className: "mt-0.5 text-xs text-muted", children: [x.label, x.telemetry ? ` · ${x.telemetry}` : ''] }), _jsxs("div", { className: "mb-1 mt-2 flex items-center justify-between gap-2 text-[11px] text-muted", children: [_jsxs("span", { children: ["DOWNLOAD \u00B7 ", x.download.toFixed(0), "%"] }), x.downloading ? _jsx("button", { type: "button", onClick: () => onSkip(x), className: "h-7 rounded-lg border border-line-2 px-2.5 text-[11px] font-bold text-fg", children: "Skip" }) : null] }), _jsx(Bar, { pct: x.download }), x.sending ? _jsxs(_Fragment, { children: [_jsxs("p", { className: "mb-1 mt-2 text-[11px] text-muted", children: ["UPLOAD \u00B7 ", x.upload.toFixed(0), "%"] }), _jsx(Bar, { pct: x.upload })] }) : null, x.processing ? _jsxs("p", { className: "mt-2 text-[11px] text-muted", children: ["PROCESSING \u00B7 ", x.label] }) : null] }));
}
/** Content Automation: RSS → qBittorrent → verify → Telegram, as the classic six-step flow with a live queue. */
export function AutomationWorkflow() {
    const cfg = useAutomationConfig();
    const channels = useSourceChannels();
    const status = useStatus();
    const qc = useQueryClient();
    const [step, setStep] = useState(0);
    const [form, setForm] = useState(DEFAULT_FORM);
    const [msg, setMsg] = useState('');
    const [busy, setBusy] = useState('');
    const [conn, setConn] = useState(null);
    const [confirm, setConfirm] = useState(null);
    const loaded = useRef(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const saved = (k) => Boolean(cfg.data?.[k]);
    useEffect(() => {
        if (cfg.data && !loaded.current) {
            loaded.current = true;
            setForm(formFromConfig(cfg.data));
        }
    }, [cfg.data]);
    const ca = (status.data?.content_automation ?? {});
    const view = queueView(ca, Number(cfg.data?.max_concurrent_torrents) || 1);
    const problem = stepProblem(step, form);
    const warnings = reviewWarnings(form);
    const canNext = step >= 4 || problem === '';
    const refresh = () => { void qc.invalidateQueries({ queryKey: ['status'] }); void qc.invalidateQueries({ queryKey: ['lanes'] }); };
    const post = (path, body) => api(`/api/operations/automation/${path}`, { method: 'POST', json: body ?? {} }).catch((e) => ({ ok: false, detail: detail(e, 'request failed') }));
    const save = async () => {
        const r = await post('config', configBody(form));
        if (r.ok) {
            setMsg('Saved.');
            loaded.current = false;
            setForm((f) => ({ ...f, qbtPass: '', qbtKey: '' }));
            await qc.invalidateQueries({ queryKey: ['automation-config'] });
        }
        else
            setMsg(`Save failed: ${r.detail || 'unknown error'}`);
        return r;
    };
    const act = async (name, fn) => {
        setBusy(name);
        try {
            await fn();
        }
        finally {
            setBusy('');
        }
    };
    const browse = () => act('browse', async () => {
        const r = await post('pick-folder', { title: 'Select Content Automation download folder', initial_path: form.path, allow_create: true });
        if (r.cancelled)
            return;
        if (r.ok && r.path) {
            set('path', r.path);
            setMsg('Download folder selected. Click Save configuration to apply it.');
        }
        else
            setMsg(`Folder selection failed: ${r.detail || 'unknown error'}`);
    });
    const saveFolder = () => act('folder', async () => {
        if (!form.path.trim())
            return void setMsg('Enter or browse to a torrent download folder first.');
        const r = await post('config', { download_path: form.path.trim() });
        if (r.ok) {
            set('path', String(r.config?.download_path ?? form.path.trim()));
            setMsg('Torrent download folder saved.');
        }
        else
            setMsg(`Save folder failed: ${r.detail || 'unknown error'}`);
    });
    const validate = () => act('test', async () => {
        const s = await save();
        if (!s.ok)
            return void setMsg('Save engine selection before testing.');
        setMsg('Testing qBittorrent...');
        const r = await post('test-engine');
        if (r.ok) {
            setMsg(`qBittorrent: ${r.version || '?'} · ${r.mode || 'ready'}${r.url ? ` · ${r.url}` : ''}${r.session_notes ? ` · ${r.session_notes}` : ''}`);
            setConn({ ok: true, text: `Connected${r.version ? ` · ${r.version}` : ''}` });
        }
        else {
            setMsg(`Download engine test failed: ${r.detail || 'unknown error'}`);
            setConn({ ok: false, text: 'Not connected' });
        }
    });
    const start = () => act('start', async () => {
        const p = startProblem(form);
        if (p)
            return void setMsg(p);
        const s = await save();
        if (!s.ok)
            return void setMsg('Save configuration before starting.');
        const r = await post('start');
        setMsg(r.ok ? `Started job ${r.id}.` : `Start failed: ${r.detail || 'unknown error'}`);
        setConn(r.ok ? { ok: true, text: `Connected${r.version ? ` · ${r.version}` : ''}` } : { ok: false, text: 'Not connected' });
        r.ok ? toast('Content Automation started') : toast.error(r.detail || 'Start failed');
        refresh();
    });
    const stop = () => act('stop', async () => {
        const r = await post('stop');
        setMsg(r.ok ? 'Stopped.' : `Stop failed: ${r.detail || 'unknown error'}`);
        refresh();
    });
    const resume = () => act('resume', async () => {
        const r = await post('resume');
        setMsg(r.ok ? 'Previous Content Automation task resumed.' : `Resume failed: ${r.detail || 'unknown error'}`);
        refresh();
    });
    const confirmed = () => act('confirm', async () => {
        const c = confirm;
        setConfirm(null);
        if (!c)
            return;
        if (c.kind === 'clear') {
            const r = await post('clear-all');
            setMsg(r.ok ? `Cleared ${Number(r.queue_items || 0).toLocaleString()} queue item(s), ${Number(r.torrents || 0).toLocaleString()} torrent(s), and automation cache.` : r.detail || 'Could not clear Content Automation.');
        }
        else if (c.kind === 'skip' && c.item) {
            const r = await post('skip-item', { item_key: c.item.key });
            setMsg(r.ok ? `Skipped ${r.title || c.item.title}.` : `Skip failed: ${r.detail || 'unknown error'}`);
        }
        else {
            const r = await post('stop');
            setMsg(r.ok ? 'Previous Content Automation task cancelled.' : `Cancel failed: ${r.detail || 'unknown error'}`);
        }
        refresh();
    });
    const row = (label, value) => _jsxs("div", { className: "grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] gap-3", children: [_jsx("dt", { className: "text-muted", children: label }), _jsx("dd", { className: "font-semibold [overflow-wrap:anywhere]", children: value })] }, label);
    const chanName = (id) => channels.data?.find((c) => c.telegram_id === id)?.title ?? id;
    const numField = (label, k, suffix, min, max) => (_jsx(Field, { label: label, children: _jsxs("span", { className: "flex items-center gap-2", children: [_jsx("input", { type: "number", min: min, max: max, value: form[k], onChange: (e) => set(k, Number(e.target.value)), className: FIELD }), _jsx("span", { className: "text-xs text-faint", children: suffix })] }) }));
    const confirmText = { clear: 'Clear all Content Automation queue, qBittorrent automation torrents, download/cache files, engine cache, jobs, and activity? This does not remove Telegram library data.', skip: `Skip "${confirm?.item?.title ?? ''}" and remove its torrent/download? It will not be processed or uploaded.`, cancel: 'Cancel the previous Content Automation task? The queue and downloaded files will be kept; use Clear all to remove them.' };
    const state = automationState(ca);
    return (_jsxs("section", { "aria-label": "Content Automation", className: CARD, children: [_jsxs("div", { className: "mb-1 flex items-start gap-3", children: [_jsx("span", { className: "grid size-10 shrink-0 place-items-center rounded-xl bg-warn/15 text-warn", children: _jsx(Icon, { name: "automation" }) }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("h2", { className: "text-lg font-extrabold", children: "Content Automation" }), _jsx("span", { className: "rounded-full bg-surface-3 px-3 py-1 text-xs text-muted", role: "status", children: state })] }), _jsx("p", { className: "text-sm text-muted", children: "RSS \u2192 qBittorrent \u2192 validate \u2192 identify local media \u2192 reuse/build individual snapshots \u2192 Telegram. Existing snapshots are reused only for the same unchanged local media; new or changed downloads build a new snapshot. qBittorrent is managed automatically by Archive X." })] })] }), _jsx(Stepper, { steps: STEPS, step: step, onStep: setStep, canGo: canNext }), step === 0 ? (_jsxs("div", { className: "grid gap-4", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "1 \u00B7 Source & destination" }), _jsx(Field, { label: "RSS feed URL", children: _jsx("input", { type: "text", value: form.rss, onChange: (e) => set('rss', e.target.value), placeholder: "https://example.com/feed.xml", "aria-label": "RSS feed URL", className: FIELD }) }), _jsx(Field, { label: "Destination channel", children: _jsxs("select", { value: form.destination, onChange: (e) => set('destination', e.target.value), "aria-label": "Destination channel", className: FIELD, children: [_jsx("option", { value: "", children: "\u2014" }), form.destination && !channels.data?.some((c) => c.telegram_id === form.destination) ? _jsx("option", { value: form.destination, children: form.destination }) : null, (channels.data ?? []).map((c) => _jsx("option", { value: c.telegram_id, children: c.title }, c.telegram_id))] }) }), _jsxs("div", { className: "rounded-xl border border-line bg-bg p-3 text-xs text-muted", children: [_jsx("b", { className: "block text-sm text-fg", children: "Telegram Delivery Policy" }), "Content Automation uses the shared Telegram delivery policy for canonical playback assets, delivery verification and cleanup. Workflow-specific destinations and source rules remain here."] }), _jsx(Field, { label: "Torrent download folder", hint: "qBittorrent will save Content Automation torrents here. The folder is created if needed.", children: _jsxs("span", { className: "flex flex-wrap gap-2", children: [_jsx("input", { type: "text", value: form.path, onChange: (e) => set('path', e.target.value), placeholder: "Downloads\\ContentAutomation", "aria-label": "Torrent download folder", className: cn(FIELD, 'min-w-[200px] flex-1') }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => void browse(), className: "h-11 rounded-xl border border-line-2 px-3 text-sm font-semibold", children: "Browse\u2026" }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => void saveFolder(), className: "h-11 rounded-xl border border-line-2 px-3 text-sm font-semibold", children: "Save folder" })] }) })] })) : step === 1 ? (_jsxs("div", { className: "grid gap-4", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "2 \u00B7 Runtime & connectivity" }), _jsx(Field, { label: "Engine", children: _jsx("select", { value: "qbittorrent", disabled: true, "aria-label": "Engine", className: FIELD, children: _jsx("option", { value: "qbittorrent", children: "qBittorrent (Web API)" }) }) }), numField('Poll interval', 'poll', 'sec', 30, 3600), _jsx("p", { className: "text-sm text-muted", children: "Each completed torrent is verified, then runs Inventory \u2192 Metadata Match \u2192 Performer Data \u2192 Channel Data \u2192 Snapshots. The database is committed before Telegram Transfer Manager delivery; source torrent/file cleanup happens only after verified delivery." }), _jsxs("div", { className: "flex items-center gap-2 rounded-xl bg-bg p-3 text-sm", children: [_jsx("i", { className: "size-2 rounded-full bg-good" }), _jsx("b", { children: "qBittorrent" }), _jsx("span", { className: "text-muted", children: "External process \u00B7 recommended for stability testing" })] }), _jsxs("details", { className: "rounded-xl border border-line px-3 py-2", children: [_jsx("summary", { className: "cursor-pointer text-sm font-semibold", children: "qBittorrent connection" }), _jsxs("div", { className: "mt-3 grid gap-3", children: [_jsx(Field, { label: "WebUI", children: _jsx("input", { type: "text", value: form.qbtUrl, onChange: (e) => set('qbtUrl', e.target.value), "aria-label": "qBittorrent WebUI address", className: FIELD }) }), _jsx(Field, { label: "Username", children: _jsx("input", { type: "text", value: form.qbtUser, autoComplete: "username", placeholder: "optional", onChange: (e) => set('qbtUser', e.target.value), "aria-label": "qBittorrent username", className: FIELD }) }), _jsx(Field, { label: "Password", children: _jsx("input", { type: "password", value: form.qbtPass, autoComplete: "current-password", placeholder: "Enter to replace saved password", onChange: (e) => set('qbtPass', e.target.value), "aria-label": "qBittorrent password", className: FIELD }) }), _jsx(Field, { label: "API key", children: _jsx("input", { type: "password", value: form.qbtKey, autoComplete: "off", placeholder: "Enter to replace saved key (qBittorrent 5.2+)", onChange: (e) => set('qbtKey', e.target.value), "aria-label": "qBittorrent API key", className: FIELD }) }), _jsxs("p", { className: "text-xs text-muted", children: ["Credentials: username", _jsx(Saved, { on: saved('qbittorrent_username_saved') }), " \u00B7 password", _jsx(Saved, { on: saved('qbittorrent_password_saved') }), " \u00B7 API key", _jsx(Saved, { on: saved('qbittorrent_api_key_saved') })] })] })] }), problem ? _jsx("p", { role: "alert", className: "text-sm text-warn", children: problem }) : null] })) : step === 2 ? (_jsxs("div", { className: "grid gap-4", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "3 \u00B7 Release rules" }), _jsx(Field, { label: "Name contains", children: _jsx("input", { type: "text", value: form.must, onChange: (e) => set('must', e.target.value), placeholder: "Optional release-name text", "aria-label": "Name contains", className: FIELD }) }), _jsx(Field, { label: "Resolution", children: _jsxs("select", { value: form.only1080, onChange: (e) => set('only1080', e.target.value), "aria-label": "Resolution", className: FIELD, children: [_jsx("option", { value: "1080p", children: "1080p releases" }), _jsx("option", { value: "any", children: "Any resolution" })] }) }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [numField('Min size', 'minSize', 'MiB', 0, 10240), numField('Max size', 'maxSize', 'MiB', 0, 10240)] }), _jsxs("div", { className: "rounded-xl border border-line p-2", children: [_jsx(Check, { checked: form.regex, onChange: (v) => set('regex', v), title: "Regex matching" }), _jsx(Check, { checked: true, disabled: true, onChange: () => undefined, title: "Remove completed torrent after verified delivery (required)" }), _jsx(Check, { checked: true, disabled: true, onChange: () => undefined, title: "Delete local download after verified delivery (required)" })] }), _jsx(Field, { label: "When a file exceeds Telegram's safe upload limit", hint: "Optimize may re-encode the video while preserving the original. Skip leaves the completed download on disk and does not remove its torrent.", children: _jsxs("select", { value: form.oversized, onChange: (e) => set('oversized', e.target.value), "aria-label": "Oversized file handling", className: FIELD, children: [_jsx("option", { value: "optimize", children: "Optimize to fit (may reduce quality; original preserved)" }), _jsx("option", { value: "skip", children: "Skip oversized files (keep local download)" })] }) }), problem ? _jsx("p", { role: "alert", className: "text-sm text-warn", children: problem }) : null] })) : step === 3 ? (_jsxs("div", { className: "grid gap-3", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "4 \u00B7 Queue & activity" }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => setConfirm({ kind: 'clear' }), className: "h-9 rounded-xl border border-line-2 px-3 text-xs font-bold text-bad hover:bg-bad/10", children: "Clear all" })] }), _jsxs("div", { className: "rounded-xl bg-bg p-3 text-sm", "aria-live": "polite", children: [_jsx("p", { children: view.summary }), view.restartPending ? (_jsxs("div", { className: "mt-3 rounded-xl border border-warn/50 bg-warn/10 p-3", children: [_jsx("b", { children: "Previous Content Automation task is paused" }), _jsx("p", { className: "mt-1 text-xs text-muted", children: "The application was closed while it was running. It will not start automatically." }), _jsxs("div", { className: "mt-2 flex flex-wrap gap-2", children: [_jsx("button", { type: "button", onClick: () => void resume(), className: "h-9 rounded-lg bg-accent px-3 text-xs font-bold text-accent-fg", children: "Resume previous task" }), _jsx("button", { type: "button", onClick: () => setConfirm({ kind: 'cancel' }), className: "h-9 rounded-lg border border-line-2 px-3 text-xs font-bold text-bad", children: "Cancel previous task" })] })] })) : null, _jsx("p", { className: "mt-2 text-xs text-muted", children: view.downloads }), view.paused ? _jsx("p", { className: "mt-2 text-xs font-bold text-warn", children: "Automation paused by operator" }) : null, view.active.length ? _jsx("ul", { className: "mt-3 grid gap-2", children: view.active.map((x) => _jsx(ActiveCard, { x: x, onSkip: (i) => setConfirm({ kind: 'skip', item: i }) }, x.key)) }) : null, view.queued.length ? (_jsxs(_Fragment, { children: [_jsxs("p", { className: "mt-3 text-[11px] font-bold text-faint", children: ["QUEUED (", view.queued.length + view.queuedMore, ")"] }), _jsx("ol", { className: "mt-1 grid gap-1", children: view.queued.map((x, i) => _jsxs("li", { className: "flex gap-2 text-xs", children: [_jsxs("span", { className: "rounded bg-surface-3 px-1.5", children: ["#", i + 1] }), _jsx("span", { className: "truncate", children: x.title })] }, x.key)) }), view.queuedMore ? _jsxs("p", { className: "text-xs text-faint", children: ["+", view.queuedMore, " more"] }) : null] })) : null, view.recent.length ? (_jsxs(_Fragment, { children: [_jsx("p", { className: "mt-3 text-[11px] font-bold text-faint", children: "RECENT" }), _jsx("ul", { className: "mt-1 grid gap-1", children: view.recent.map((x) => _jsxs("li", { className: "flex gap-2 text-xs", children: [_jsx("span", { className: "rounded bg-surface-3 px-1.5", children: x.status }), _jsx("span", { className: "truncate", children: x.title })] }, x.key)) })] })) : null] })] })) : step === 4 ? (_jsxs("div", { children: [_jsx("h3", { className: "mb-2 text-[15px] font-bold", children: "5 \u00B7 Review" }), _jsx("p", { className: cn('mb-3 text-sm', warnings.length ? 'text-warn' : 'text-muted'), role: warnings.length ? 'alert' : undefined, children: warnings.length ? warnings.join(' ') : 'Configuration looks valid. Confirm the settings, then continue to Run.' }), _jsxs("dl", { className: "grid gap-1.5 rounded-xl bg-bg p-3 text-sm", children: [row('RSS feed', form.rss || '—'), row('Destination', form.destination ? chanName(form.destination) : '—'), row('Download folder', form.path || '—'), row('Download adapter', 'qBittorrent'), row('Delivery mode', 'Serial'), row('Poll interval', `${form.poll} sec`), row('Resolution', form.only1080 === '1080p' ? '1080p only' : 'Any'), row('Must contain', form.must || '—'), row('Min size', `${form.minSize} MiB`), row('Max size', `${form.maxSize} MiB`)] })] })) : (_jsxs("div", { className: "grid gap-3", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "6 \u00B7 Run" }), _jsx("p", { className: "text-sm text-muted", children: "Start the configured automation or test the selected download engine. Active work remains visible in Operations and the task tray." }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("button", { type: "button", disabled: !!busy, onClick: () => void save(), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Save configuration" }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => void validate(), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: busy === 'test' ? 'Validating…' : 'Validate qBittorrent' }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => void start(), className: "h-11 rounded-xl bg-accent px-5 text-sm font-extrabold text-accent-fg disabled:opacity-60", children: busy === 'start' ? 'Starting…' : 'Start automation' }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => void stop(), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold text-bad", children: "Stop" })] }), _jsxs("p", { className: "flex items-center gap-2 text-xs text-muted", role: "status", children: [_jsx("i", { className: cn('size-2 rounded-full', conn ? (conn.ok ? 'bg-good' : 'bg-bad') : 'bg-surface-3') }), conn?.text ?? 'Not verified'] })] })), _jsx("p", { className: "mt-3 min-h-5 text-xs text-muted", "aria-live": "polite", children: msg }), _jsxs("div", { className: "mt-3 flex items-center justify-between gap-2", children: [_jsx("button", { type: "button", disabled: step === 0, onClick: () => setStep(step - 1), className: "h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-40", children: "Back" }), _jsxs("span", { className: "flex gap-2", children: [step < 5 ? _jsx("button", { type: "button", disabled: !!busy, onClick: () => void save(), className: "h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Save configuration" }) : null, step < 5 ? _jsx("button", { type: "button", disabled: !canNext, onClick: () => setStep(step + 1), className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50", children: "Next" }) : null] })] }), confirm ? (_jsx(Dialog, { open: true, onClose: () => setConfirm(null), title: confirm.kind === 'clear' ? 'Clear all automation data' : confirm.kind === 'skip' ? 'Skip this download' : 'Cancel previous automation', children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: confirmText[confirm.kind] }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setConfirm(null), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Keep" }), _jsx("button", { type: "button", onClick: () => void confirmed(), className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white", children: confirm.kind === 'clear' ? 'Clear all' : confirm.kind === 'skip' ? 'Skip download' : 'Cancel task' })] })] }) })) : null] }));
}
