import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useDiagnostics, useResources, useStatus } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { AnimatePresence, listItem, motion } from '@/design/motion';
import { useCountTo } from '@/design/AnimatedNumber';
import { Icon } from '@/design/Icon';
import { PageHead } from '@/design/PageHead';
import { Segmented } from '@/design/Segmented';
import { Skeleton, SkeletonGroup } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { JobControls } from './JobControls';
import { JobDetails } from './JobDetails';
import { PerformerAudit } from './PerformerAudit';
import { AutoMatchReview, useAutoMatches } from './AutoMatchReview';
import { LibraryHealth } from './LibraryHealth';
import { SceneMatchCard } from './SceneMatchCard';
import { ChannelDuplicateCard } from './ChannelDuplicates';
import { ResourceControls } from './ResourceControls';
import { idOf } from './jobs';
import { attention, diagnosticTiles, filterJobs, filterLog, healthTiles, jobCounts, laneRows, policyLine, resourceCards, telemetryTiles } from './ops';
const CARD = 'rounded-[18px] border border-line bg-surface p-4 sm:p-5';
const TONE = { ok: 'text-good', warn: 'text-warn', bad: 'text-bad', neutral: 'text-fg' };
const stamp = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
function Metric({ dot, value, label }) {
    return (_jsxs("div", { className: "rounded-[14px] border border-line bg-surface px-4 py-3", children: [_jsx("strong", { className: "block text-2xl font-extrabold tabular-nums", children: value }), _jsxs("span", { className: "flex items-center gap-1.5 text-xs text-muted", children: [_jsx("i", { className: cn('size-1.5 rounded-full', dot) }), label] })] }));
}
function TileGrid({ tiles, cols = 'grid-cols-2' }) {
    return (_jsx("dl", { className: cn('grid gap-2.5', cols), children: tiles.map((t) => (_jsxs("div", { className: "min-w-0 rounded-xl bg-bg px-3.5 py-3", children: [_jsx("dt", { className: "text-xs text-muted", children: t.label }), _jsx("dd", { className: cn('mt-0.5 truncate text-[15px] font-bold', TONE[t.tone]), title: t.value, children: t.value })] }, t.label))) }));
}
const Card = ({ title, aside, children, className }) => (_jsxs("section", { "aria-label": title, className: cn(CARD, className), children: [_jsxs("div", { className: "mb-3.5 flex items-center justify-between gap-3", children: [_jsx("h3", { className: "text-lg font-extrabold", children: title }), _jsx("span", { className: "text-xs text-faint", children: aside })] }), children] }));
const statusTone = (st) => (/^(RUNNING|ACTIVE)$/.test(st) ? 'ok' : /^(FAILED|ERROR|CANCELLED)$/.test(st) ? 'bad' : /^(QUEUED|PAUSED|WAITING|RETRYING|COMPLETED_WITH_ERRORS)$/.test(st) ? 'warn' : 'neutral');
/** The jobs-list progress: files done of total when the job counts them, else the job's own percentage; "not yet measurable" only when it has neither. */
export function listProgress(j) {
    const total = Number(j.total) || 0;
    const done = Math.max(0, Number(j.completed) || 0);
    if (total > 0) {
        const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
        return { known: true, percent, text: `${percent}% · ${done.toLocaleString()} / ${total.toLocaleString()}` };
    }
    const p = Number(j.progress);
    if (Number.isFinite(p) && p > 0) {
        const percent = Math.max(0, Math.min(100, p));
        return { known: true, percent, text: `${percent.toFixed(percent < 10 ? 1 : 0)}%` };
    }
    return { known: false, percent: 0, text: 'progress not yet measurable' };
}
function JobRow({ j }) {
    const prog = listProgress(j);
    const rolling = useCountTo(Math.round(prog.percent));
    const progText = prog.known && /^\d+%/.test(prog.text) ? prog.text.replace(/^\d+%/, `${rolling}%`) : prog.text;
    const st = String(j.status ?? '').toUpperCase();
    const [open, setOpen] = useState(false);
    const id = idOf(j);
    const detailed = Boolean(id) && String(j.type ?? '') !== 'PLAYBACK'; // a playback session has no job record
    return (_jsxs(motion.li, { ...listItem, onClick: (e) => { if (detailed && !e.target.closest('button,a,dialog,[role=group]'))
            setOpen(true); }, className: cn('flex flex-col rounded-xl bg-bg p-3.5', detailed && 'cursor-pointer hover:bg-fg/[0.03]'), children: [_jsxs("div", { className: "flex items-center justify-between gap-3", children: [detailed ? _jsx("button", { type: "button", onClick: () => setOpen(true), title: "Open for pipeline, worker, timing and error details", "aria-label": `Details for ${String(j.name || j.type || 'task')}`, className: "min-w-0 truncate text-left font-bold hover:underline", children: String(j.name || j.type || 'Task') }) : _jsx("span", { className: "min-w-0 truncate font-bold", children: String(j.name || j.type || 'Task') }), _jsx("span", { className: cn('shrink-0 rounded-full bg-surface-3 px-2.5 py-0.5 text-[11px] font-bold', TONE[statusTone(st)]), children: st.replace(/_/g, ' ') })] }), _jsx("p", { className: "mt-0.5 h-4 truncate text-xs leading-4 text-muted", title: String(j.operation || ''), children: String(j.operation || '') }), _jsx("div", { className: "mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3", role: "progressbar", "aria-label": "Progress", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": prog.known ? prog.percent : undefined, children: _jsx("span", { className: cn('block h-full rounded-full bg-accent transition-[width] duration-500', !prog.known && 'w-1/3 animate-pulse'), style: prog.known ? { width: `${prog.percent}%` } : undefined }) }), _jsx("p", { className: "mt-1 text-[11px] tabular-nums text-faint", children: progText }), _jsx(JobControls, { j: j }), open ? _jsx(JobDetails, { id: id, onClose: () => setOpen(false) }) : null] }));
}
const JOB_TABS = [{ value: 'all', label: 'All' }, { value: 'running', label: 'Running' }, { value: 'queued', label: 'Queued' }, { value: 'failed', label: 'Failed' }, { value: 'completed', label: 'Completed' }];
const LEVEL_TABS = [{ value: '', label: 'All' }, { value: 'INFO', label: 'Info' }, { value: 'WARN', label: 'Warning' }, { value: 'ERROR', label: 'Error' }, { value: 'SUCCESS', label: 'Success' }];
const MODULES = ['Telegram Transfer Manager', 'Metadata & Assets Manager', 'Content Automation', 'Backup & Restore', 'playback', 'other'];
const OUTCOMES = ['EXPECTED', 'SUCCESS', 'TRANSIENT', 'DEGRADED', 'FAILURE', 'FATAL'];
const SELECT = 'h-10 rounded-xl border border-line bg-surface px-3 text-sm font-medium';
const dot = (lv) => (lv === 'ERROR' || lv === 'CRITICAL' ? 'bg-bad' : lv === 'WARN' || lv === 'WARNING' ? 'bg-warn' : lv === 'SUCCESS' ? 'bg-good' : 'bg-accent');
function ActivityLog({ events }) {
    const [level, setLevel] = useState('');
    const [module, setModule] = useState('');
    const [outcome, setOutcome] = useState('');
    const [query, setQuery] = useState('');
    const rows = useMemo(() => filterLog(events, { level, module, outcome, query }), [events, level, module, outcome, query]);
    const box = useRef(null);
    const atEnd = useRef(true);
    // Oldest to newest like the classic log; stay on the newest entry unless the reader scrolled up.
    useEffect(() => {
        const el = box.current;
        if (el && atEnd.current)
            el.scrollTop = el.scrollHeight;
    }, [rows]);
    return (_jsxs("section", { "aria-label": "Recent activity", className: CARD, children: [_jsx("h3", { className: "mb-3.5 text-lg font-extrabold", children: "Recent activity" }), _jsxs("div", { className: "mb-3 flex flex-wrap items-center gap-2", children: [_jsx(Segmented, { label: "Log level", value: level, onChange: setLevel, options: LEVEL_TABS }), _jsxs("select", { "aria-label": "Log source", value: module, onChange: (e) => setModule(e.target.value), className: SELECT, children: [_jsx("option", { value: "", children: "All modules" }), MODULES.map((m) => _jsx("option", { children: m }, m))] }), _jsxs("select", { "aria-label": "Filter diagnostic outcome", value: outcome, onChange: (e) => setOutcome(e.target.value), className: SELECT, children: [_jsx("option", { value: "", children: "All outcomes" }), OUTCOMES.map((m) => _jsx("option", { value: m, children: m[0] + m.slice(1).toLowerCase() }, m))] }), _jsxs("label", { className: "flex h-10 min-w-[200px] flex-1 items-center gap-2 rounded-xl border border-line bg-bg px-3 text-faint", children: [_jsx(Icon, { name: "search", className: "size-4" }), _jsx("input", { value: query, onChange: (e) => setQuery(e.target.value), placeholder: "Search visible logs\u2026", "aria-label": "Search visible logs", className: "min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-faint" })] })] }), _jsxs("ul", { ref: box, onScroll: (e) => { const t = e.currentTarget; atEnd.current = t.scrollTop + t.clientHeight >= t.scrollHeight - 30; }, className: "max-h-[420px] overflow-y-auto rounded-xl bg-bg", "aria-label": "Log entries", children: [rows.map((e, i) => {
                        const lv = String(e.level ?? 'INFO').toUpperCase();
                        const t = e.created_at ?? e.ts;
                        const time = t ? new Date(String(t)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '';
                        return (_jsxs("li", { className: "grid grid-cols-[48px_10px_minmax(70px,130px)_minmax(0,1fr)] items-center gap-3 border-b border-line px-3 py-2.5 text-[13px] last:border-0", children: [_jsx("time", { className: "tabular-nums text-faint", children: time }), _jsx("i", { className: cn('size-2 rounded-full', dot(lv)), "aria-label": lv }), _jsx("span", { className: "truncate text-muted", children: String(e.module ?? '') }), _jsx("span", { className: "min-w-0 truncate", title: String(e.message ?? ''), children: String(e.message ?? '') })] }, `${String(e.event_key ?? i)}:${i}`));
                    }), !rows.length ? _jsx("li", { className: "p-6 text-center text-sm text-muted", children: "No log entries match." }) : null] })] }));
}
function MaintenanceLink({ title, text, label, onOpen }) {
    return (_jsxs("div", { className: "flex flex-col rounded-xl border border-line bg-bg p-4", children: [_jsx("h5", { className: "text-[15px] font-bold", children: title }), _jsx("p", { className: "mb-3 mt-1 text-xs leading-relaxed text-muted", children: text }), onOpen ? _jsx("button", { type: "button", onClick: onOpen, className: "mt-auto inline-flex h-9 w-fit items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5", children: label }) : _jsxs("a", { href: "/#admin", className: "inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5", children: [label, _jsx(Icon, { name: "external", className: "size-3.5" })] })] }));
}
export function OperationsPage() {
    const status = useStatus();
    const diag = useDiagnostics();
    const res = useResources();
    const qc = useQueryClient();
    const [tab, setTab] = useState('all');
    const [confirmReset, setConfirmReset] = useState(false);
    const [audit, setAudit] = useState(false);
    const [review, setReview] = useState(false);
    const autoMatches = useAutoMatches();
    const st = (status.data ?? {});
    const jobs = (Array.isArray(st.jobs) ? st.jobs : []);
    const counts = jobCounts(jobs);
    const shown = filterJobs(jobs, tab);
    const resources = (res.data?.resources ?? {});
    const updated = stamp();
    const exportHttp = async () => {
        try {
            const r = await fetch('/api/diagnostics/metrics/export', { credentials: 'same-origin', cache: 'no-store' });
            if (!r.ok)
                throw new Error('export failed');
            const url = URL.createObjectURL(await r.blob());
            const a = document.createElement('a');
            a.href = url;
            a.download = /filename="?([^";]+)"?/i.exec(r.headers.get('content-disposition') ?? '')?.[1] ?? 'archive-x-http-telemetry.json';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            toast('HTTP telemetry window exported.');
        }
        catch {
            toast.error('Could not export HTTP telemetry.');
        }
    };
    const resetHttp = async () => {
        try {
            await api('/api/diagnostics/metrics/reset', { method: 'POST', json: {} });
            await qc.invalidateQueries({ queryKey: ['diagnostics'] });
            toast('HTTP telemetry window reset.');
        }
        catch (e) {
            toast.error(e instanceof ApiError ? e.detail || 'Could not reset HTTP telemetry.' : 'Could not reset HTTP telemetry.');
        }
        finally {
            setConfirmReset(false);
        }
    };
    if (status.isPending)
        return (_jsx("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: _jsxs(SkeletonGroup, { label: "Loading operations", className: "grid gap-4", children: [_jsx("div", { className: "grid grid-cols-2 gap-3 sm:grid-cols-5", children: Array.from({ length: 5 }, (_, i) => _jsx(Skeleton, { className: "h-[68px] rounded-[14px]" }, i)) }), _jsx(Skeleton, { className: "h-52 rounded-[18px]" }), _jsx("div", { className: "grid gap-4 lg:grid-cols-3", children: Array.from({ length: 3 }, (_, i) => _jsx(Skeleton, { className: "h-60 rounded-[18px]" }, i)) })] }) }));
    const tele = telemetryTiles(st);
    return (_jsxs("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(PageHead, { eyebrow: "Control Center", title: "Operations", blurb: "Service health, resources, diagnostics and live jobs", children: _jsxs("span", { className: "inline-flex h-7 items-center gap-1.5 rounded-full bg-good/15 px-3 text-xs font-bold text-good", children: [_jsx("i", { className: "size-1.5 rounded-full bg-good" }), "Live"] }) }), _jsxs("div", { className: "mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5", role: "group", "aria-label": "Job summary", children: [_jsx(Metric, { dot: "bg-accent", value: counts.running, label: "Running" }), _jsx(Metric, { dot: "bg-warn", value: counts.queued, label: "Queued" }), _jsx(Metric, { dot: "bg-bad", value: counts.failed, label: "Failed" }), _jsx(Metric, { dot: "bg-good", value: counts.completed, label: "Completed" }), _jsx(Metric, { dot: "bg-accent", value: counts.withErrors, label: "With errors" })] }), _jsxs(Card, { title: "Jobs", className: "mb-4", children: [_jsx("div", { className: "mb-3", children: _jsx(Segmented, { label: "Job filter", value: tab, onChange: setTab, options: JOB_TABS }) }), shown.length ? _jsx("ul", { className: "grid grid-cols-[minmax(0,1fr)] auto-rows-fr gap-2.5 md:grid-cols-2", children: _jsx(AnimatePresence, { initial: false, children: shown.map((j, i) => _jsx(JobRow, { j: j }, String(j.id ?? j.job_id ?? i))) }) }) : _jsx("p", { className: "py-3 text-sm text-muted", children: tab === 'all' ? 'No active jobs.' : 'No jobs match this filter.' })] }), _jsxs("div", { className: "mb-4 grid gap-4 lg:grid-cols-3", children: [_jsx(Card, { title: "System health", aside: `Updated ${updated}`, children: _jsx(TileGrid, { tiles: healthTiles(st) }) }), _jsx(Card, { title: "Attention", aside: "operator action", children: _jsx("ul", { className: "grid gap-2", children: attention(st, autoMatches.data?.count ?? autoMatches.data?.items?.length ?? 0).map((x) => (_jsxs("li", { className: "flex items-start gap-3 rounded-xl bg-bg p-3 text-sm", children: [_jsx("span", { className: cn('mt-0.5 grid size-8 shrink-0 place-items-center rounded-full', x.tone === 'bad' ? 'bg-bad/15 text-bad' : x.tone === 'warn' ? 'bg-warn/15 text-warn' : 'bg-good/15 text-good'), children: _jsx(Icon, { name: x.tone === 'ok' ? 'check' : 'alert', className: "size-4" }) }), x.action ? _jsx("button", { type: "button", onClick: () => setReview(true), className: "self-center text-left font-semibold text-accent underline-offset-2 hover:underline", children: x.text }) : _jsx("span", { className: "self-center", children: x.text })] }, x.key))) }) }), _jsx(Card, { title: "Transfer telemetry", aside: _jsx("span", { className: "rounded-full bg-surface-3 px-2.5 py-0.5 text-[11px] font-bold text-muted", children: tele.rate }), children: _jsx(TileGrid, { tiles: tele.tiles }) })] }), _jsx(Card, { title: "Diagnostics", className: "mb-4", aside: _jsxs("span", { className: "flex gap-2", children: [_jsx("button", { type: "button", onClick: () => void exportHttp(), className: "h-8 rounded-lg border border-line-2 px-3 text-xs font-bold text-fg hover:bg-fg/5", children: "Export HTTP window" }), _jsx("button", { type: "button", onClick: () => setConfirmReset(true), className: "h-8 rounded-lg border border-line-2 px-3 text-xs font-bold text-fg hover:bg-fg/5", children: "Reset HTTP window" })] }), children: diag.isPending ? _jsx("p", { className: "text-sm text-muted", children: "Loading diagnostics\u2026" }) : diag.isError ? _jsx("p", { role: "alert", className: "text-sm text-warn", children: "Diagnostics are unavailable right now." }) : _jsx(TileGrid, { tiles: diagnosticTiles((diag.data ?? {})), cols: "grid-cols-2 md:grid-cols-4" }) }), _jsxs("section", { "aria-label": "Library maintenance", className: cn(CARD, 'mb-4'), children: [_jsx("p", { className: "text-[11px] font-bold uppercase tracking-[0.12em] text-accent", children: "Library care" }), _jsx("h2", { className: "text-2xl font-extrabold tracking-tight", children: "Library Maintenance" }), _jsx("p", { className: "mb-4 mt-1 max-w-3xl text-[15px] text-muted", children: "Check library health and review duplicates or unmatched records. Fix issues to keep your library organized and up to date." }), _jsxs("div", { className: "grid gap-4 lg:grid-cols-3", children: [_jsxs("div", { className: "flex flex-col gap-3 rounded-2xl border border-line p-4 [&>*:not(h4)]:flex-1", children: [_jsx("h4", { className: "text-lg font-extrabold lg:min-h-[3.4rem] xl:min-h-0", children: "Health & Previews" }), _jsx(LibraryHealth, {})] }), _jsxs("div", { className: "flex flex-col gap-3 rounded-2xl border border-line p-4 [&>*:not(h4)]:flex-1", children: [_jsx("h4", { className: "text-lg font-extrabold lg:min-h-[3.4rem] xl:min-h-0", children: "Duplicates" }), _jsx(MaintenanceLink, { title: "Duplicate performers", text: "Compare names, aliases and artwork, then review the evidence before merging.", label: "Open performer review", onOpen: () => setAudit(true) }), _jsx(ChannelDuplicateCard, {})] }), _jsxs("div", { className: "flex flex-col gap-3 rounded-2xl border border-line p-4 [&>*:not(h4)]:flex-1", children: [_jsx("h4", { className: "text-lg font-extrabold lg:min-h-[3.4rem] xl:min-h-0", children: "Metadata & scene matches" }), _jsx(SceneMatchCard, {})] })] })] }), _jsxs(Card, { title: "Resource Controller", className: "mb-4", aside: _jsx("span", { className: "rounded-full bg-surface-3 px-2.5 py-1 text-xs font-bold text-muted", children: String(o2(resources.control).mode_label || 'Automatic') }), children: [_jsx("p", { className: "-mt-2 mb-3 text-sm text-muted", children: "Control how playback, background work, Telegram transfers and worker capacity share the machine." }), res.isError || !Object.keys(resources).length ? (_jsx("p", { role: res.isError ? 'alert' : 'status', className: "text-sm text-warn", children: res.isPending ? 'Loading resource status…' : 'Resource status is unavailable. Retrying automatically.' })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: "mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4", children: resourceCards(resources, st).map((c) => (_jsxs("div", { className: "min-w-0 rounded-xl bg-bg p-4", children: [_jsx("p", { className: "text-xs text-muted", children: c.title }), _jsx("p", { className: cn('my-1 text-xl font-extrabold', TONE[c.tone]), children: c.value }), _jsx("p", { className: "text-xs leading-relaxed text-muted [overflow-wrap:anywhere]", children: c.sub })] }, c.title))) }), _jsx("ul", { className: "grid gap-2.5", "aria-label": "Resource lanes", children: laneRows(resources).map((l) => (_jsxs("li", { className: "grid grid-cols-[88px_minmax(0,1fr)_44px] items-center gap-3 text-sm", children: [_jsx("b", { className: "font-semibold", children: l.label }), _jsx("span", { className: "h-1.5 overflow-hidden rounded-full bg-surface-3", role: "progressbar", "aria-label": `${l.label} lane`, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": l.pct, children: _jsx("i", { className: "block h-full rounded-full bg-accent transition-[width]", style: { width: `${l.pct}%` } }) }), _jsxs("span", { className: "text-right text-xs tabular-nums text-muted", children: [l.active, " / ", l.limit] })] }, l.key))) }), _jsx("p", { className: "mt-3 text-xs text-faint", children: policyLine(resources, st, updated) }), _jsx(ResourceControls, { resources: resources })] }))] }), _jsx(ActivityLog, { events: Array.isArray(st.events) ? st.events : [] }), audit ? _jsx(PerformerAudit, { onClose: () => setAudit(false) }) : null, review ? _jsx(AutoMatchReview, { onClose: () => setReview(false) }) : null, confirmReset ? (_jsx(Dialog, { open: true, onClose: () => setConfirmReset(false), title: "Reset HTTP telemetry", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: "Reset the bounded HTTP telemetry window? This clears only the in-memory HTTP request counters and latency samples." }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setConfirmReset(false), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: () => void resetHttp(), className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white", children: "Reset telemetry" })] })] }) })) : null] }));
}
const o2 = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
