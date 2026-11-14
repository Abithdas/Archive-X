import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { usePreviewManifest } from '@/api/hooks';
import { Confirm } from '@/design/Confirm';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { ALL_FAILED, ALL_INDEXED, CHOOSE_SCOPE, DELETE_FAILED, SCAN_FAILED, SNAPSHOT_FRAMES, candidateKey, channelName, confirmCopy, deleteBody, deletePlan, doneToast, formatDuration, formatResolution, formatSize, frameUrl, introText, progressText, resultWarnings, scanSummary, scanTitles, scanUrl, scanningText, scopeOptions, selectionState, stoppedStatus, stoppedToast, validIds } from './dupes';
const BTN = 'inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const reason = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb);
/** Four cached frames for one copy; they load only once the card scrolls into view. */
export function Frames({ cid, mid, title }) {
    const ref = useRef(null);
    const [seen, setSeen] = useState(typeof IntersectionObserver !== 'function');
    useEffect(() => {
        const el = ref.current;
        if (seen || !el)
            return;
        const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) {
            setSeen(true);
            io.disconnect();
        } }, { rootMargin: '200px 0px', threshold: 0.01 });
        io.observe(el);
        return () => io.disconnect();
    }, [seen]);
    const m = usePreviewManifest(cid, mid, seen);
    const rev = String(m.data?.revision ?? m.data?.updated_at ?? '');
    const note = !seen ? 'Loading cached frame…' : m.isPending ? 'Loading cached frame…' : m.isError ? 'Preview unavailable' : m.data?.frames_ready ? '' : 'No cached snapshot';
    return (_jsx("div", { ref: ref, className: "grid grid-cols-2 gap-2 sm:grid-cols-4", "aria-label": "Four cached snapshots", children: SNAPSHOT_FRAMES.map((f, i) => (_jsxs("figure", { className: "m-0", children: [_jsx("div", { className: "relative grid aspect-video place-items-center overflow-hidden rounded-lg bg-surface-3 text-center text-[11px] text-muted", children: m.data?.frames_ready ? _jsx("img", { alt: `Cached snapshot ${i + 1} of ${title}`, decoding: "async", loading: "lazy", src: frameUrl(cid, mid, f, rev), className: "absolute inset-0 size-full object-cover", onError: (e) => { e.currentTarget.style.display = 'none'; } }) : _jsx("span", { children: note }) }), _jsxs("figcaption", { className: "mt-1 text-[10px] text-faint", children: ["Frame ", f + 1, " / 36"] })] }, f))) }));
}
function Copy({ row, group, saved, picked, disabled, onToggle }) {
    const mid = Number(row.message_id || 0);
    const cid = String(row.channel_id ?? '');
    const title = String(row.title || row.filename || `Video ${mid}`);
    const channel = channelName(cid, row.channel_title, saved);
    return (_jsxs("article", { "data-group": group, className: cn('grid gap-3 rounded-xl border bg-bg p-3', picked ? 'border-bad' : 'border-line'), children: [_jsxs("div", { className: "flex flex-wrap items-start gap-3", children: [_jsxs("label", { className: "inline-flex shrink-0 items-center gap-2 text-sm font-semibold", children: [_jsx("input", { type: "checkbox", checked: picked, disabled: disabled, onChange: onToggle, "aria-label": `Select ${title} from ${channel}, message ${mid}, to delete` }), _jsx("span", { children: "Select to delete" })] }), _jsxs("div", { className: "min-w-0 flex-1 text-left", children: [_jsx("b", { className: "block truncate text-sm", title: title, children: title }), _jsxs("div", { className: "text-xs text-muted", children: [channel, " \u00B7 Message ID ", _jsx("b", { children: mid })] }), _jsx("dl", { className: "mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs", "aria-label": "Video properties", children: [['Resolution', formatResolution(row.width, row.height)], ['File size', formatSize(row.size)], ['Duration', formatDuration(row.duration)]].map(([k, v]) => _jsxs("div", { children: [_jsxs("dt", { className: "inline text-faint", children: [k, " "] }), _jsx("dd", { className: "inline font-semibold", children: v })] }, k)) })] })] }), _jsx(Frames, { cid: cid, mid: mid, title: title })] }));
}
/** Verified duplicate video review. Scanning changes nothing; each delete is confirmed, checked against a kept copy, and run one at a time. */
function DuplicateDialog({ scope, saved, onClose }) {
    const [scan, setScan] = useState(null);
    const [status, setStatus] = useState(scanningText(scope === ALL_INDEXED));
    const [busy, setBusy] = useState(true);
    const [deleting, setDeleting] = useState(false);
    const [note, setNote] = useState('');
    const [picked, setPicked] = useState(new Set());
    const [ask, setAsk] = useState(null);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const all = scope === ALL_INDEXED;
    const run = useCallback(async () => {
        setBusy(true);
        setPicked(new Set());
        setScan(null);
        setStatus(scanningText(all));
        try {
            const r = await api(scanUrl(scope));
            if (!alive.current)
                return;
            if (!r || r.ok !== true)
                throw new ApiError(0, r?.detail || (all ? ALL_FAILED : SCAN_FAILED));
            setScan(r);
        }
        catch (e) {
            if (alive.current)
                setStatus(reason(e, all ? ALL_FAILED : SCAN_FAILED));
        }
        finally {
            if (alive.current)
                setBusy(false);
        }
    }, [scope, all]);
    useEffect(() => { void run(); }, [run]);
    const groups = scan?.groups ?? [];
    const sel = selectionState(groups, picked, deleting || busy);
    const titles = scan ? scanTitles(scan, saved) : { title: all ? 'Duplicate videos across channels' : 'Duplicate videos', context: 'Verified duplicate review' };
    const sum = scan ? scanSummary(scan) : null;
    const toggle = (key) => setPicked((s) => { const n = new Set(s); if (n.has(key))
        n.delete(key);
    else
        n.add(key); return n; });
    const start = () => {
        const plan = deletePlan(groups, picked, saved);
        if (!plan?.length)
            return void toast('Leave one directly matching copy unselected in each duplicate group.');
        const c = confirmCopy(plan);
        setAsk({ title: c.title, text: c.text, ok: c.ok, run: () => void (async () => {
                setDeleting(true);
                const warnings = [];
                let deleted = 0, failure = '', failedAt = null, attempted = false;
                for (let i = 0; i < plan.length; i++) {
                    const item = plan[i];
                    const channel = channelName(item.row.channel_id, item.row.channel_title, saved);
                    const mid = Number(item.row.message_id || 0);
                    if (alive.current)
                        setStatus(progressText(i, plan.length, channel, mid));
                    try {
                        const body = deleteBody(item);
                        if (!validIds(body))
                            throw new Error('A selected channel or message ID is invalid.');
                        attempted = true;
                        const r = await api('/api/channel-maintenance/delete-duplicate', { method: 'POST', json: body });
                        if (!r || r.ok !== true)
                            throw new Error(r?.detail || 'Telegram did not confirm the deletion.');
                        deleted++;
                        warnings.push(...resultWarnings(r));
                    }
                    catch (e) {
                        failedAt = { channel, mid };
                        failure = reason(e, DELETE_FAILED);
                        break;
                    }
                }
                if (alive.current)
                    setDeleting(false);
                if (attempted && alive.current)
                    await run();
                if (failure) {
                    toast(stoppedToast(deleted, plan.length, failedAt?.channel ?? '', failedAt?.mid ?? '', failure), { duration: 10000 });
                    if (alive.current)
                        setNote(stoppedStatus(deleted, plan.length, failure));
                }
                else
                    toast(doneToast(deleted, c.channels, warnings), { duration: 10000 });
            })() });
    };
    return (_jsxs(Dialog, { wide: true, open: true, onClose: () => { if (deleting)
            toast('The delete batch is still running. Wait for it to finish before closing this review.');
        else
            onClose(); }, title: titles.title, footer: _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3", children: [_jsxs("div", { "aria-live": "polite", className: "min-w-0 text-left", children: [_jsx("b", { className: "block text-sm", children: sel.countText }), _jsx("small", { className: "text-xs text-muted", children: sel.hint })] }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsxs("button", { type: "button", disabled: !sel.canDelete, onClick: start, className: "inline-flex h-9 items-center gap-2 rounded-xl bg-bad px-3 text-sm font-bold text-white disabled:opacity-50", children: [_jsx(Icon, { name: "trash", className: "size-4" }), "Delete", sel.count ? ` ${sel.count}` : '', " selected"] }), _jsxs("button", { type: "button", disabled: busy || deleting, onClick: () => void run(), className: BTN, children: [_jsx(Icon, { name: "refresh", className: "size-4" }), "Scan again"] }), _jsx("button", { type: "button", disabled: deleting, onClick: onClose, className: BTN, children: "Close" })] })] }), children: [_jsxs("div", { className: "grid gap-3 text-left", children: [_jsxs("p", { className: "text-xs text-muted", children: [titles.context, " \u2014 ", introText(all)] }), sum ? (_jsxs("div", { role: "status", "aria-live": "polite", className: "grid gap-1 text-sm", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 text-xs font-semibold", children: [_jsx("span", { className: sum.warnings.length ? 'text-warn' : 'text-good', children: deleting ? status : sum.state }), _jsxs("span", { className: "rounded-full bg-surface-2 px-2.5 py-1", children: [sum.groups, " group", sum.groups === 1 ? '' : 's'] }), _jsxs("span", { className: "rounded-full bg-surface-2 px-2.5 py-1", children: [sum.copies, " ", sum.copies === 1 ? 'copy' : 'copies'] }), _jsx("span", { className: "rounded-full bg-surface-2 px-2.5 py-1", children: sum.all ? 'Across channels' : channelName(scan?.channel_id, scan?.channel_title, saved) })] }), _jsx("div", { className: "text-xs text-muted", children: sum.detail }), note ? _jsx("div", { className: "text-xs font-semibold text-warn", children: note }) : null, sum.warnings.length ? _jsxs("div", { className: "rounded-lg border border-warn/50 p-2 text-xs text-muted", children: [sum.warnings.map((w, i) => _jsx("div", { children: w }, i)), scan?.fingerprint_warnings_truncated ? _jsx("div", { children: "Additional fingerprint warnings were omitted." }) : null] }) : null] })) : _jsx("p", { role: "status", "aria-live": "polite", className: "text-sm text-muted", children: status }), busy && !scan ? _jsx("div", { className: "rounded-2xl border border-line p-3.5 text-sm text-muted", children: all ? 'Reading saved video identities across channels…' : 'Reading the saved channel inventory and verifying duplicate content identities against Telegram…' }) : null, sum && !groups.length ? _jsxs("div", { className: "rounded-2xl border border-line p-3.5", children: [_jsx("b", { children: "No verified duplicate videos found." }), _jsxs("p", { className: "mt-1 text-xs text-muted", children: [sum.emptyNote, " No Telegram messages were changed."] })] }) : null, groups.map((g, gi) => {
                        const rows = g.candidates ?? [];
                        return (_jsxs("section", { "aria-label": `Group ${gi + 1}`, className: "grid gap-2 rounded-2xl border border-line p-3", children: [_jsxs("div", { className: "flex flex-wrap items-baseline justify-between gap-2", children: [_jsxs("h5", { className: "text-sm font-bold", children: ["Group ", gi + 1, " ", _jsxs("span", { className: "font-normal text-muted", children: ["\u00B7 ", Number(g.duplicate_count || rows.length), " matching copies"] })] }), _jsx("span", { className: "text-[11px] text-accent", children: (g.proof ?? []).join(' · ') })] }), rows.map((r) => { const k = candidateKey(r); return _jsx(Copy, { row: r, group: gi, saved: saved, picked: picked.has(k), disabled: deleting || busy || (!picked.has(k) && !sel.canSelectMore), onToggle: () => toggle(k) }, k); })] }, gi));
                    })] }), ask ? _jsx(Confirm, { title: ask.title, text: ask.text, ok: ask.ok, danger: true, onCancel: () => setAsk(null), onOk: () => { const go = ask.run; setAsk(null); go(); } }) : null] }));
}
/** The Operations card: pick a channel (or all indexed videos) and open the review. */
export function ChannelDuplicateCard() {
    const q = useQuery({ queryKey: ['source-channels'], queryFn: () => api('/api/source-channels'), staleTime: 30_000 });
    const [scope, setScope] = useState('');
    const [open, setOpen] = useState(null);
    const saved = Array.isArray(q.data) ? q.data : [];
    const options = scopeOptions(saved);
    const go = () => { if (!scope)
        return void toast(CHOOSE_SCOPE); setOpen(scope); };
    return (_jsxs("div", { className: "flex flex-col rounded-xl border border-line bg-bg p-4", children: [_jsx("h5", { className: "text-[15px] font-bold", children: "Duplicate video review" }), _jsx("p", { className: "mb-3 mt-1 text-xs leading-relaxed text-muted", children: "Choose a channel or all indexed videos to scan for verified duplicate video posts. Nothing is changed until you select copies and confirm." }), _jsxs("div", { className: "mt-auto flex flex-wrap items-center gap-2", children: [_jsx("label", { htmlFor: "dup-scope", className: "text-xs font-semibold text-muted", children: "Channel to scan" }), _jsx("select", { id: "dup-scope", "aria-label": "Duplicate scan scope", value: scope, disabled: q.isPending, onChange: (e) => setScope(e.target.value), className: "h-9 min-w-0 max-w-full rounded-xl border border-line-2 bg-surface px-2 text-sm", children: q.isPending ? _jsx("option", { value: "", children: "Loading channels\u2026" }) : q.isError ? _jsx("option", { value: "", children: "Channel list unavailable \u2014 retry" }) : options.map((o) => _jsx("option", { value: o.value, children: o.label }, o.value)) }), _jsxs("button", { type: "button", onClick: go, className: "inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5", children: ["Scan for duplicates", _jsx(Icon, { name: "arrow-right", className: "size-3.5" })] })] }), q.isError ? _jsx("button", { type: "button", onClick: () => void q.refetch(), className: "mt-2 text-xs font-semibold text-accent underline", children: "Retry" }) : null, open ? _jsx(DuplicateDialog, { scope: open, saved: saved, onClose: () => setOpen(null) }, open) : null] }));
}
