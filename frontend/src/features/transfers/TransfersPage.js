import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useChannels, useSourceChannels, useLanes } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { PageHead } from '@/design/PageHead';
import { Segmented } from '@/design/Segmented';
import { Skeleton } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { CARD, Check, Stepper, detail, videosHint } from './parts';
import { AutomationWorkflow } from './AutomationWorkflow';
import { TransferWorkflow } from './TransferWorkflow';
import { AnimatePresence, listItem, motion } from '@/design/motion';
import { ChannelAvatar } from '@/features/channels/ChannelAvatar';
import { activeLaneCount, jobPath, laneChips, toLanes } from './lanes';
import { DEFAULT_OPTIONS, joinLaunch, pendingBody, photoFailure, photoLaunch, photoMode, photoRequest, prerequisitePlan, processBody, selectedStages, stageLabels, expandStages } from './metadata';
function LaneCard({ lane, busy, onAct }) {
    const label = { pause: 'Pause', resume: 'Resume', cancel: 'Stop', recover: 'Recover & Resume' };
    return (_jsx(motion.article, { ...listItem, "data-kind": lane.kind, className: "rounded-xl bg-bg p-3.5", children: _jsxs("div", { className: "flex items-start gap-3", children: [_jsx("span", { className: "grid size-9 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent", children: _jsx(Icon, { name: lane.kind === 'metadata' ? 'database' : 'transfer' }) }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("strong", { className: "truncate text-[15px]", children: lane.title }), _jsx("span", { className: "shrink-0 rounded-full bg-surface-3 px-2.5 py-0.5 text-[11px] font-bold capitalize text-muted", children: lane.stopping ? 'stopping…' : lane.statusLabel })] }), _jsx("p", { className: "truncate text-xs text-muted", title: lane.subtitle, children: lane.subtitle }), lane.waiting ? _jsx("p", { role: "status", className: "truncate text-xs font-semibold text-warn", title: lane.waiting, children: lane.waiting }) : null, lane.detail ? _jsx("p", { className: "truncate text-xs text-faint", children: lane.detail }) : null, _jsx("div", { className: "mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3", role: "progressbar", "aria-label": `${lane.title} progress`, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(lane.pct), children: _jsx("span", { className: "block h-full rounded-full bg-accent transition-[width]", style: { width: `${lane.pct}%` } }) }), _jsxs("div", { className: "mt-1 flex items-center justify-between gap-2", children: [_jsxs("span", { className: "text-[11px] tabular-nums text-faint", children: [Math.round(lane.pct), "%"] }), _jsx("span", { className: "flex gap-1.5", children: lane.actions.map((a) => _jsx("button", { type: "button", disabled: busy, onClick: () => onAct(lane, a), className: cn('h-8 rounded-lg px-3 text-xs font-bold disabled:opacity-50', a === 'cancel' ? 'border border-line-2 text-bad hover:bg-bad/10' : 'bg-accent text-accent-fg'), children: label[a] }, a)) })] })] })] }) }));
}
function LiveLanes() {
    const lanes = useLanes();
    const channels = useSourceChannels();
    const qc = useQueryClient();
    const [busy, setBusy] = useState('');
    const [stop, setStop] = useState(null);
    const nameOf = (ids) => String(ids ?? '').split(',').map((x) => x.trim()).filter(Boolean).map((id) => channels.data?.find((c) => c.telegram_id === id)?.title ?? id).join(', ');
    const jobs = lanes.data?.jobs ?? [];
    const list = useMemo(() => toLanes(jobs, nameOf), [jobs, channels.data]); // eslint-disable-line react-hooks/exhaustive-deps
    const act = async (l, a) => {
        if (a === 'cancel')
            return setStop(l);
        setBusy(l.id);
        try {
            const r = await api(jobPath(l.id, a), { method: 'POST' }).catch((e) => ({ ok: false, detail: detail(e, '') }));
            if (r.ok === false)
                toast.error(r.detail || `Could not ${a} job`);
            else
                toast(a === 'pause' ? (r.status === 'PAUSED' ? 'Job paused.' : 'Pause requested; the current safe operation will finish first.') : a === 'resume' ? `Job ${String(r.status ?? 'resuming').toLowerCase()}.` : 'Job recovered and queued from its saved state.');
        }
        finally {
            setBusy('');
            void qc.invalidateQueries({ queryKey: ['lanes'] });
            void qc.invalidateQueries({ queryKey: ['status'] });
        }
    };
    const confirmStop = async () => {
        const l = stop;
        if (!l)
            return;
        setStop(null);
        setBusy(l.id);
        try {
            const r = await api(`/api/jobs/${encodeURIComponent(l.id)}/cancel`, { method: 'POST' });
            if (r?.ok === false)
                toast.error(r.detail || 'Could not stop job');
            else
                toast(r?.status === 'CANCELLED' ? 'Job stopped.' : 'Stop requested; Archive X is finishing cancellation safely.');
        }
        catch (e) {
            toast.error(detail(e, 'Could not stop job'));
        }
        finally {
            setBusy('');
            void qc.invalidateQueries({ queryKey: ['lanes'] });
            void qc.invalidateQueries({ queryKey: ['status'] });
        }
    };
    const chips = laneChips(lanes.data?.governor ?? {}, lanes.data?.resources ?? {});
    const n = activeLaneCount(jobs);
    return (_jsxs("section", { "aria-label": "Live lanes", className: cn(CARD, 'mb-4'), children: [_jsxs("div", { className: "mb-3 flex items-baseline justify-between gap-3", children: [_jsx("h2", { className: "text-xl font-extrabold", children: "Live lanes" }), _jsx("span", { className: "text-xs text-faint", children: "Playback keeps priority" })] }), lanes.isPending ? (_jsx("div", { role: "status", "aria-label": "Loading lanes", className: "flex flex-wrap gap-2", children: Array.from({ length: 5 }, (_, i) => _jsx(Skeleton, { className: "h-9 w-40 rounded-full" }, i)) })) : lanes.isError ? (_jsx("p", { role: "alert", className: "text-sm text-warn", children: "Live lane data is unavailable. Retrying automatically." })) : (_jsxs(_Fragment, { children: [_jsx("ul", { className: "mb-3 flex flex-wrap gap-2", "aria-label": "Resource usage", children: chips.map((c) => _jsxs("li", { className: "rounded-full bg-surface-3 px-3.5 py-2 text-xs text-muted", children: [c.label, " ", _jsx("strong", { className: "ml-1 text-fg", children: c.value })] }, c.key)) }), _jsxs("p", { className: "mb-3 text-xs text-faint", role: "status", children: [n, " active/queued lane job", n === 1 ? '' : 's'] }), list.length ? _jsx("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-2.5 md:grid-cols-2", children: _jsx(AnimatePresence, { initial: false, children: list.map((l) => _jsx(LaneCard, { lane: l, busy: busy === l.id, onAct: (x, a) => void act(x, a) }, l.key)) }) }) : _jsx("p", { className: "text-sm text-muted", children: "No active lane jobs. Start a transfer, local upload, manual Metadata & Assets scan, or Content Automation to see its independent progress here." })] })), stop ? (_jsx(Dialog, { open: true, onClose: () => setStop(null), title: "Stop job", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: "Stop this active job? Archive X will cancel it cooperatively first; if it does not stop within the grace period, the job supervisor will escalate cancellation. Verified completed items remain saved." }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setStop(null), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: () => void confirmStop(), className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white", children: "Stop job" })] })] }) })) : null] }));
}
const STEPS = ['Channels', 'Processing Stages', 'Review', 'Run'];
function MetadataWorkflow() {
    const channels = useSourceChannels();
    const library = useChannels();
    // a saved channel has no video count of its own; the library's count is used when the channel is published
    const libraryCount = useMemo(() => new Map((Array.isArray(library.data) ? library.data : []).map((c) => [c.telegram_id, c.video_count])), [library.data]);
    const qc = useQueryClient();
    const [step, setStep] = useState(0);
    const [q, setQ] = useState('');
    const [picked, setPicked] = useState([]);
    const [o, setO] = useState(DEFAULT_OPTIONS);
    const [busy, setBusy] = useState('');
    const [msg, setMsg] = useState('');
    const [ask, setAsk] = useState(null);
    const list = (channels.data ?? []).filter((c) => c.title.toLowerCase().includes(q.trim().toLowerCase()));
    const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
    const set = (k, v) => setO({ ...o, [k]: v });
    const stages = selectedStages(o);
    const needChannels = !picked.length;
    const needStages = !stages.length && !photoMode(o);
    const canNext = step === 0 ? !needChannels : step === 1 ? !needStages : true;
    const refreshAll = () => { void qc.invalidateQueries({ queryKey: ['lanes'] }); void qc.invalidateQueries({ queryKey: ['status'] }); };
    /** Queues the image scan / reconciliation job (if chosen) and returns its launch sentence; a failure stops the run with the classic wording. */
    const launchPhotos = async () => {
        const mode = photoMode(o);
        if (!mode)
            return '';
        const req = photoRequest(mode, picked);
        const r = await api(req.url, { method: 'POST', json: req.body }).catch((e) => { throw new ApiError(0, photoFailure(mode, e instanceof ApiError ? e.detail : e instanceof Error ? e.message : '')); });
        if (!r || r.ok === false || !r.id)
            throw new ApiError(0, photoFailure(mode, r?.detail ?? ''));
        return photoLaunch(mode, r.id, picked.length);
    };
    const run = async () => {
        if (needChannels)
            return void setMsg('Select at least one channel.');
        if (needStages)
            return void setMsg('Select at least one processing stage.');
        setBusy('run');
        setMsg('');
        try {
            let metaText = '';
            const finish = async () => {
                let photoText = '';
                try {
                    photoText = await launchPhotos();
                }
                catch (e) {
                    const t = joinLaunch(metaText, detail(e, 'Could not queue the image job'));
                    setMsg(t);
                    toast.error(t);
                    refreshAll();
                    return;
                }
                const all = joinLaunch(metaText, photoText);
                if (photoText) {
                    setMsg(all);
                    toast(all);
                }
                refreshAll();
            };
            if (!stages.length) {
                await finish();
                return;
            }
            const pf = await api('/api/metadata-assets/preflight', { method: 'POST', json: { channel_ids: picked, stages } });
            if (!pf.ok)
                throw new ApiError(0, `Could not validate prerequisites: ${pf.detail || 'unknown error'}`);
            const start = async (runStages, autoAdded) => {
                const r = await api('/api/metadata-assets/process-channel', { method: 'POST', json: processBody(picked, runStages, o, autoAdded) });
                if (!r.ok)
                    throw new ApiError(0, `Could not start Metadata & Assets: ${r.detail || 'unknown error'}`);
                metaText = r.already_running ? r.message || 'A Metadata & Assets job is already running.' : `Metadata & Assets queued${r.id ? ` as job ${r.id}` : ''}: ${stageLabels(runStages).join(' → ')}${o.fullInventory ? ' → older-message + uploaded-caption reconciliation' : ''}`;
                setMsg(metaText);
                toast(metaText);
                if (!r.already_running)
                    setO((x) => ({ ...x, forceEntity: false }));
                await finish();
            };
            if (pf.ready === false) {
                const plan = prerequisitePlan(pf, stages);
                setAsk({ labels: plan.labels, affected: plan.affected, run: () => void start(plan.stages, plan.autoAdded).catch((e) => { setMsg(detail(e, 'Could not start')); toast.error(detail(e, 'Could not start')); }).finally(() => setBusy('')) });
                return;
            }
            await start(expandStages(stages), {});
        }
        catch (e) {
            const t = detail(e, 'Could not start Metadata & Assets');
            setMsg(t);
            toast.error(t);
        }
        finally {
            setBusy((b) => (b === 'run' && ask ? b : ''));
        }
    };
    const pending = async () => {
        if (needChannels)
            return void setMsg('Select at least one channel.');
        if (!selectedStages(o, true).length)
            return void setMsg('Select at least one processing stage (Inventory is intentionally skipped).');
        setBusy('pending');
        try {
            const r = await api('/api/metadata-assets/process-pending', { method: 'POST', json: pendingBody(picked, o) });
            const t = !r.ok ? `Could not start: ${r.detail || 'unknown error'}` : r.no_pending ? r.message || 'No pending Library items.' : r.already_running ? r.message || 'A Metadata & Assets job is already running.' : r.message || 'Pending Library items queued.';
            setMsg(t);
            r.ok ? toast(t) : toast.error(t);
            refreshAll();
        }
        catch (e) {
            const t = detail(e, 'Could not start pending processing');
            setMsg(t);
            toast.error(t);
        }
        finally {
            setBusy('');
        }
    };
    return (_jsxs("section", { "aria-label": "Metadata and Assets Manager", className: CARD, children: [_jsxs("div", { className: "mb-1 flex items-start gap-3", children: [_jsx("span", { className: "grid size-10 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent", children: _jsx(Icon, { name: "database" }) }), _jsxs("div", { children: [_jsx("h2", { className: "text-lg font-extrabold", children: "Metadata & Assets Manager" }), _jsx("p", { className: "text-sm text-muted", children: "Select channels and run the recommended refresh. Use Advanced options only when you need a custom workflow." })] })] }), _jsx(Stepper, { steps: STEPS, step: step, onStep: setStep, canGo: canNext }), step === 0 ? (_jsxs("div", { children: [_jsx("h3", { className: "mb-1 text-[15px] font-bold", children: "1 \u00B7 Channels" }), _jsx("p", { className: "mb-3 text-sm text-muted", children: "Choose the Telegram channels that should be inventoried and refreshed." }), _jsxs("div", { className: "mb-2 flex gap-2", children: [_jsx("input", { type: "search", value: q, onChange: (e) => setQ(e.target.value), placeholder: "Search channels\u2026", "aria-label": "Search channels", className: "h-10 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3 text-sm" }), _jsx("button", { type: "button", onClick: () => setPicked((channels.data ?? []).map((c) => c.telegram_id)), className: "h-10 rounded-xl border border-line-2 px-3 text-xs font-bold", children: "Select all" }), _jsx("button", { type: "button", onClick: () => setPicked([]), className: "h-10 rounded-xl border border-line-2 px-3 text-xs font-bold", children: "Clear" })] }), _jsx("div", { role: "group", "aria-label": "Metadata and Assets channels", className: "max-h-56 overflow-auto rounded-xl border border-line bg-bg p-1", children: channels.isPending ? _jsx("p", { className: "p-3 text-sm text-muted", children: "Loading channels\u2026" }) : list.length ? list.map((c) => _jsx(Check, { checked: picked.includes(c.telegram_id), onChange: () => toggle(c.telegram_id), title: c.title, icon: _jsx(ChannelAvatar, { id: c.telegram_id, title: c.title, image: c.image }), hint: videosHint(libraryCount.get(c.telegram_id) ?? c.video_count) }, c.telegram_id)) : _jsx("p", { className: "p-3 text-sm text-muted", children: "No channels match." }) }), _jsxs("p", { className: "mt-2 text-xs text-muted", "aria-live": "polite", children: ["Selected: ", picked.length, " channel", picked.length === 1 ? '' : 's'] })] })) : step === 1 ? (_jsxs("div", { className: "grid gap-4", children: [_jsxs("div", { children: [_jsx("h3", { className: "mb-1 text-[15px] font-bold", children: "2 \u00B7 Processing Stages" }), _jsx("p", { className: "text-sm text-muted", children: "The recommended refresh updates metadata, performer and studio details, and video snapshots." })] }), _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Scope", _jsxs("select", { value: o.scope, onChange: (e) => set('scope', e.target.value), "aria-label": "Videos to process", className: "h-11 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg", children: [_jsx("option", { value: "all", children: "Entire channel (recommended)" }), ['25', '50', '100', '250'].map((n) => _jsxs("option", { value: n, children: ["First ", n, " videos"] }, n))] })] }), _jsxs("div", { className: "rounded-xl border border-line p-2", children: [_jsx("p", { className: "px-2 pb-1 pt-1 text-xs font-bold text-muted", children: "Processing stages" }), _jsx(Check, { checked: o.inventory, onChange: (v) => set('inventory', v), title: "Inventory", hint: "Collect filenames and Telegram media information." }), _jsx(Check, { checked: o.entities, onChange: (v) => set('entities', v), title: "Metadata & Entity Data", hint: "Match video metadata, then collect linked performer details, artwork, and studio/site data in one pipeline." }), _jsx(Check, { checked: o.snapshots, onChange: (v) => set('snapshots', v), title: "Snapshots", hint: "Generate or repair the 36-frame playback snapshots." }), _jsx(Check, { checked: o.imageScan, onChange: (v) => set('imageScan', v), title: "Image scan", hint: "Scan the selected Telegram channels' full message history for photos and image documents. This is read-only and runs independently of the video scope limit." }), _jsx(Check, { checked: o.photoReconcile, onChange: (v) => set('photoReconcile', v), title: "Reconcile deleted Telegram images", hint: "Check indexed image message IDs and remove local entries, cached files, thumbnails, and face-analysis records only when Telegram confirms the message is missing or no longer contains an image. This does not rescan channel history." })] }), _jsxs("div", { className: "rounded-xl border border-line p-2", children: [_jsx("p", { className: "px-2 pb-1 pt-1 text-xs font-bold text-muted", children: "Recheck options" }), _jsx(Check, { checked: o.forceMetadata, onChange: (v) => set('forceMetadata', v), title: "Force metadata recheck" }), _jsx(Check, { checked: o.forceEntity, onChange: (v) => set('forceEntity', v), title: "Force performer/studio recheck for this run" }), _jsx(Check, { checked: o.fullInventory, onChange: (v) => set('fullInventory', v), title: "Reconcile", hint: "Checks older Telegram history and reconciles captions of delivered posts. No media re-upload." })] })] })) : step === 2 ? (_jsxs("div", { children: [_jsx("h3", { className: "mb-2 text-[15px] font-bold", children: "3 \u00B7 Review" }), _jsx("dl", { className: "grid gap-1.5 rounded-xl bg-bg p-3 text-sm", children: [['Channels', picked.map((id) => channels.data?.find((c) => c.telegram_id === id)?.title ?? id).join(', ') || '—'], ['Scope', o.scope === 'all' ? 'Entire channel' : `First ${o.scope} videos`], ['Stages', [...stageLabels(stages), ...(o.imageScan ? ['Image scan'] : []), ...(o.photoReconcile ? ['Reconcile deleted Telegram images'] : [])].join(' → ') || '—'], ['Force metadata recheck', o.forceMetadata ? 'Yes' : 'No'], ['Force performer/studio recheck', o.forceEntity ? 'Yes' : 'No'], ['Reconcile older history', o.fullInventory ? 'Yes' : 'No']].map(([k, v]) => (_jsxs("div", { className: "grid grid-cols-[minmax(0,160px)_minmax(0,1fr)] gap-3", children: [_jsx("dt", { className: "text-muted", children: k }), _jsx("dd", { className: "font-semibold [overflow-wrap:anywhere]", children: v })] }, k))) }), needChannels || needStages ? _jsx("p", { role: "alert", className: "mt-2 text-sm text-warn", children: needChannels ? 'Select at least one channel.' : 'Select at least one processing stage.' }) : _jsx("p", { className: "mt-2 text-sm text-muted", children: "Configuration looks valid. Continue to Run." })] })) : (_jsxs("div", { className: "grid gap-3", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "4 \u00B7 Run" }), _jsxs("div", { className: "flex flex-wrap items-start gap-4", children: [_jsxs("div", { className: "grid gap-1", children: [_jsx("button", { type: "button", disabled: !!busy, onClick: () => void run(), className: "h-11 rounded-xl bg-accent px-6 text-sm font-extrabold text-accent-fg disabled:opacity-60", children: busy === 'run' ? 'Starting…' : 'Run' }), _jsx("small", { className: "max-w-[220px] text-xs text-muted", children: "Runs the selected channels using the chosen scope and processing stages." })] }), _jsxs("div", { className: "grid gap-1", children: [_jsx("button", { type: "button", disabled: !!busy, onClick: () => void pending(), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-bold disabled:opacity-60", children: busy === 'pending' ? 'Starting…' : 'Run Pending (no scan)' }), _jsx("small", { className: "max-w-[240px] text-xs text-muted", children: "Processes saved channel/message IDs only; it does not read Telegram history." })] })] }), _jsx("p", { className: "min-h-5 text-xs text-muted", "aria-live": "polite", children: msg })] })), _jsxs("div", { className: "mt-auto flex justify-between gap-2 pt-5", children: [_jsx("button", { type: "button", disabled: step === 0, onClick: () => setStep(step - 1), className: "h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-40", children: "Back" }), step < 3 ? _jsx("button", { type: "button", disabled: !canNext, onClick: () => setStep(step + 1), className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50", children: ['Next: Processing Stages →', 'Next: Review →', 'Next: Run →'][step] }) : _jsx("span", {})] }), ask ? (_jsx(Dialog, { open: true, onClose: () => { setAsk(null); setBusy(''); }, title: "Add required stages", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: "The selected stages require persisted prerequisite data that is not available yet." }), _jsxs("dl", { className: "grid gap-1 text-sm", children: [_jsxs("div", { children: [_jsx("dt", { className: "inline text-muted", children: "Required stage(s): " }), _jsx("dd", { className: "inline font-semibold", children: ask.labels.join(', ') })] }), _jsxs("div", { children: [_jsx("dt", { className: "inline text-muted", children: "Affected channel(s): " }), _jsx("dd", { className: "inline font-semibold", children: ask.affected.join(', ') })] })] }), _jsx("p", { className: "text-sm text-muted", children: "Add the required stage(s) before running the selected stages?" }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => { setAsk(null); setBusy(''); }, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: () => { const a = ask; setAsk(null); a.run(); }, className: "h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg", children: "Add stages and continue" })] })] }) })) : null] }));
}
export function TransfersPage() {
    const [tab, setTab] = useState('transfer');
    const show = (t) => (tab === t ? 'flex min-w-0 flex-col [&>section]:flex-1' : 'hidden min-w-0 flex-col lg:flex [&>section]:flex-1');
    return (_jsxs("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(PageHead, { eyebrow: "Transfer Workspace", title: "Transfers", blurb: "Upload local videos or images, move Telegram media and run release automation.", children: _jsxs("span", { className: "inline-flex h-7 items-center gap-1.5 rounded-full bg-accent/15 px-3 text-xs font-bold text-accent", children: [_jsx("i", { className: "size-1.5 rounded-full bg-accent" }), "Shared job queue"] }) }), _jsx(LiveLanes, {}), _jsx("div", { className: "mb-4 lg:hidden", children: _jsx(Segmented, { label: "Workflow", value: tab, onChange: setTab, options: [{ value: 'transfer', label: 'Transfer' }, { value: 'metadata', label: 'Metadata' }, { value: 'automation', label: 'Automation' }] }) }), _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] items-stretch gap-4 lg:grid-cols-2", children: [_jsx("div", { className: show('transfer'), children: _jsx(TransferWorkflow, {}) }), _jsx("div", { className: show('metadata'), children: _jsx(MetadataWorkflow, {}) })] }), _jsx("div", { className: cn('mt-4', show('automation')), children: _jsx(AutomationWorkflow, {}) })] }));
}
