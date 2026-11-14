import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Skeleton } from '@/design/Skeleton';
import { withWidth } from '@/design/responsive';
import { thumbUrl } from '@/api/client';
import { BULK_FAILED, EMPTY, FIRST_100_NOTE, NEED_SELECTION, NOTE, ONE_FAILED, TOO_MANY, BULK_MAX, allFirstSelected, appliedLine, bulkBody, bulkDone, firstBatch, oneBody, oneDone, selectedLabel } from './automatch';
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
export const useAutoMatches = (enabled = true) => useQuery({ queryKey: ['auto-matches'], enabled, queryFn: () => api('/api/metadata/auto-match-review'), staleTime: 15_000, retry: false });
function Row({ it, busy, picked, onPick, onAct }) {
    const [thumb, setThumb] = useState(true);
    return (_jsxs("li", { className: "flex flex-wrap items-start gap-3 rounded-xl border border-line bg-bg p-3", children: [thumb ? _jsx("img", { src: withWidth(thumbUrl(String(it.channel_id), it.message_id), 240), alt: "", loading: "lazy", className: "h-14 w-24 shrink-0 rounded-lg bg-surface-2 object-cover", onError: () => setThumb(false) }) : _jsx("span", { className: "h-14 w-24 shrink-0 rounded-lg bg-surface-2" }), _jsxs("div", { className: "min-w-0 flex-1 text-left", children: [_jsxs("label", { className: "flex items-center gap-2 text-sm font-semibold", children: [_jsx("input", { type: "checkbox", checked: picked, onChange: onPick, "aria-label": `Select ${it.filename || 'video'} for bulk review` }), _jsx("span", { className: "truncate", title: it.filename, children: it.filename || '(no filename)' })] }), _jsx("p", { className: "mt-1 text-xs [overflow-wrap:anywhere]", children: appliedLine(it) }), _jsx("p", { className: "mt-0.5 text-xs text-muted", children: it.reason })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => onAct('keep'), "aria-label": `Keep match for ${it.filename || 'video'}`, children: "Keep" }), _jsx("button", { type: "button", className: "h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50", disabled: busy, onClick: () => onAct('undo'), "aria-label": `Undo match for ${it.filename || 'video'}`, children: "Undo" })] })] }));
}
/** Review of the filename matches that were applied automatically before V29.4.132. */
export function AutoMatchReview({ onClose }) {
    const q = useAutoMatches();
    const qc = useQueryClient();
    const items = q.data?.items ?? [];
    const [picked, setPicked] = useState(new Set());
    const [busy, setBusy] = useState(new Set());
    const [bulkBusy, setBulkBusy] = useState(false);
    const done = () => { void qc.invalidateQueries({ queryKey: ['auto-matches'] }); void qc.invalidateQueries({ queryKey: ['library'] }); };
    const toggle = (id) => setPicked((s) => { const n = new Set(s); if (n.has(id))
        n.delete(id);
    else
        n.add(id); return n; });
    const act = async (it, a) => {
        setBusy((b) => new Set(b).add(it.video_id));
        try {
            const r = await api(`/api/metadata/auto-match-review/${it.video_id}`, { method: 'POST', json: oneBody(a) });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || ONE_FAILED);
            qc.setQueryData(['auto-matches'], (d) => ({ ...(d ?? {}), items: (d?.items ?? []).filter((x) => x.video_id !== it.video_id), count: Math.max(0, (d?.items?.length ?? 1) - 1) }));
            setPicked((s) => { const n = new Set(s); n.delete(it.video_id); return n; });
            toast(oneDone(a));
            done();
        }
        catch (e) {
            toast(e instanceof ApiError && e.detail ? e.detail : ONE_FAILED);
        }
        finally {
            setBusy((b) => { const n = new Set(b); n.delete(it.video_id); return n; });
        }
    };
    const bulk = async (a) => {
        const ids = [...picked];
        if (!ids.length)
            return void toast(NEED_SELECTION);
        if (ids.length > BULK_MAX)
            return void toast(TOO_MANY);
        setBulkBusy(true);
        try {
            const r = await api('/api/metadata/auto-match-review/bulk', { method: 'POST', json: bulkBody(a, ids) });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || BULK_FAILED);
            setPicked(new Set());
            await q.refetch();
            done();
            toast(bulkDone(a, r));
        }
        catch (e) {
            toast(e instanceof ApiError && e.detail ? e.detail : BULK_FAILED);
        }
        finally {
            setBulkBusy(false);
        }
    };
    const selectFirst = (on) => { setPicked(new Set(on ? firstBatch(items) : [])); if (on && items.length > BULK_MAX)
        toast(FIRST_100_NOTE); };
    return (_jsx(Dialog, { wide: true, open: true, onClose: onClose, title: "Earlier automatic matches to review", children: _jsxs("div", { className: "grid gap-3 text-left", children: [_jsx("p", { className: "text-xs text-muted", children: NOTE }), _jsxs("div", { className: "flex flex-wrap items-center gap-3", children: [_jsxs("label", { className: "inline-flex items-center gap-2 text-sm", children: [_jsx("input", { type: "checkbox", checked: allFirstSelected(items, picked), onChange: (e) => selectFirst(e.target.checked) }), "Select first 100"] }), _jsx("span", { className: "text-sm text-muted", children: selectedLabel(picked.size) }), _jsx("button", { type: "button", className: BTN, disabled: bulkBusy, onClick: () => void bulk('keep'), children: "Keep selected" }), _jsx("button", { type: "button", className: "h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50", disabled: bulkBusy, onClick: () => void bulk('undo'), children: "Undo selected" })] }), q.isPending ? _jsx(Skeleton, { className: "h-40 w-full rounded-xl" }) : q.isError ? _jsx("p", { role: "alert", className: "text-sm text-bad", children: "Could not load the review list." }) : !items.length ? _jsx("p", { className: "text-sm text-muted", children: EMPTY })
                    : _jsx("ul", { className: "grid gap-2", children: items.map((it) => _jsx(Row, { it: it, busy: busy.has(it.video_id) || bulkBusy, picked: picked.has(it.video_id), onPick: () => toggle(it.video_id), onAct: (a) => void act(it, a) }, it.video_id)) })] }) }));
}
