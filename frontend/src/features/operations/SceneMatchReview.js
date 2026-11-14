import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Confirm } from '@/design/Confirm';
import { Dialog } from '@/design/Dialog';
import { Skeleton } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { QuickEditDialog } from '@/features/watch/QuickEditDialog';
import { Frames } from './ChannelDuplicates';
import { DEFAULT_THRESHOLD, MAX_SELECTION, NO_FILTERS, approveAboveCopy, approveUrl, approvedToast, clamp100, countText, distinctReason, editItem, isFiltered, merge, missingLabel, needs, nextOffsets, oneDone, oneFailed, providerBadge, rejectUrl, rejectedToast, reviewUrl, safeHttp, selectLimit, thresholdOf, visibleText } from './scenematch';
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50';
const why = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb);
function SuggestionCard({ s, picked, busy, onPick, onAct }) {
    const score = clamp100(s.score);
    const image = safeHttp(s.provider_image), url = safeHttp(s.provider_url), siteUrl = safeHttp(s.provider_site_url);
    const site = String(s.provider_site || s.studio || '').trim();
    const people = (s.performers ?? []).filter(Boolean).join(', ');
    const meta = [people, s.release_date].filter(Boolean).join(' · ');
    const local = String(s.filename || s.local_title || s.candidate_title || 'video');
    const cid = String(s.channel_id ?? '');
    const title = s.candidate_title || '(untitled candidate)';
    return (_jsxs("article", { "aria-label": title, className: "grid gap-3 rounded-2xl border border-line p-3 text-left sm:grid-cols-[minmax(0,1fr)_auto]", children: [_jsxs("div", { className: "grid min-w-0 gap-2", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 text-sm", children: [_jsxs("label", { className: "inline-flex items-center gap-1.5 text-xs font-semibold text-muted", children: [_jsx("input", { type: "checkbox", checked: picked, disabled: busy, onChange: onPick, "aria-label": `Select ${title} for rejection` }), "Select"] }), _jsxs("span", { className: "rounded-full bg-accent/15 px-2 py-0.5 text-xs font-bold text-accent", children: [score.toFixed(1), "%"] }), _jsx("b", { className: "min-w-0 [overflow-wrap:anywhere]", children: title }), providerBadge(s) ? _jsx("span", { className: "rounded-full bg-surface-3 px-2 py-0.5 text-[11px] font-semibold text-muted", children: providerBadge(s) }) : null] }), _jsxs("div", { className: "flex flex-wrap gap-x-3 text-xs text-muted", children: [_jsx("span", { children: s.channel_title || 'Channel' }), _jsx("span", { children: s.filename || s.local_title || 'Untitled video' }), s.message_id ? _jsxs("span", { children: ["Message #", s.message_id] }) : null] }), _jsxs("div", { className: "flex flex-wrap gap-x-3 gap-y-1 text-xs", children: [_jsxs("span", { children: [_jsx("b", { children: "Site" }), " ", site ? (siteUrl ? _jsx("a", { href: siteUrl, target: "_blank", rel: "noopener noreferrer", className: "text-accent underline", children: site }) : site) : 'Not listed'] }), s.provider_record_id ? _jsxs("span", { children: [_jsx("b", { children: "Record ID" }), " ", s.provider_record_id] }) : null, s.website_variant_conflict ? _jsx("span", { className: "rounded-full bg-warn/15 px-2 text-warn", children: "Possible same content \u00B7 separate sites" }) : null, url ? _jsx("a", { href: url, target: "_blank", rel: "noopener noreferrer", className: "font-semibold text-accent underline", children: "Open provider record" }) : null] }), needs(s.missing_fields).length ? _jsxs("div", { className: "flex flex-wrap items-center gap-1.5 text-xs", children: [_jsx("span", { className: "text-muted", children: "Needs" }), needs(s.missing_fields).map((f) => _jsx("span", { className: "rounded-full bg-warn/15 px-2 py-0.5 font-semibold text-warn", children: f }, f))] }) : null, _jsxs("div", { className: "grid gap-3 lg:grid-cols-[160px_minmax(0,1fr)]", children: [_jsxs("div", { children: [_jsx("p", { className: "mb-1 text-[11px] text-muted", children: "Provider scene" }), image ? _jsx("img", { src: image, alt: "Provider scene candidate", loading: "lazy", referrerPolicy: "no-referrer", className: "aspect-video w-full rounded-lg bg-surface-3 object-cover", onError: (e) => (e.currentTarget.style.display = 'none') }) : _jsx("p", { className: "grid aspect-video place-items-center rounded-lg bg-surface-3 text-xs text-muted", children: "No provider image" })] }), _jsxs("div", { children: [_jsx("p", { className: "mb-1 text-[11px] text-muted", children: "Video snapshots \u00B7 4 of 36" }), s.message_id ? _jsx(Frames, { cid: cid, mid: Number(s.message_id), title: local }) : null] })] }), _jsxs("details", { className: "text-xs", children: [_jsxs("summary", { className: "cursor-pointer font-semibold", children: ["Why this match? ", _jsx("span", { className: "font-normal text-muted", children: "Optional details" })] }), _jsxs("div", { className: "mt-1 grid gap-1.5 text-muted", children: [meta ? _jsxs("p", { children: [_jsx("b", { className: "text-fg", children: "Candidate metadata" }), _jsx("br", {}), meta] }) : null, s.signals ? _jsxs("p", { children: [_jsx("b", { className: "text-fg", children: "Signals" }), _jsx("br", {}), s.signals] }) : null, distinctReason(s.signals, s.reason) ? _jsxs("p", { children: [_jsx("b", { className: "text-fg", children: "Additional note" }), _jsx("br", {}), s.reason] }) : null, !meta && !s.signals && !distinctReason(s.signals, s.reason) ? _jsx("p", { children: "No additional match details." }) : null] })] })] }), _jsxs("div", { className: "flex gap-2 sm:grid sm:content-start", children: [_jsx("button", { type: "button", className: PRIMARY, disabled: busy, onClick: () => onAct('approve'), "aria-label": `Approve ${title}`, children: "Approve" }), _jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => onAct('reject'), "aria-label": `Reject ${title}`, children: "Reject" })] })] }));
}
function UnresolvedCard({ u, onEdit }) {
    const title = u.title || u.filename || '(untitled video)';
    const current = [u.studio_name, (u.performers ?? []).filter(Boolean).join(', ')].filter(Boolean).join(' · ');
    return (_jsxs("article", { "aria-label": title, className: "grid gap-2 rounded-2xl border border-line p-3 text-left sm:grid-cols-[minmax(0,1fr)_auto]", children: [_jsxs("div", { className: "grid min-w-0 gap-1.5", children: [_jsxs("div", { className: "flex flex-wrap items-baseline gap-2", children: [_jsx("b", { className: "[overflow-wrap:anywhere]", children: title }), _jsx("span", { className: "text-xs text-muted", children: "Needs metadata" })] }), _jsxs("p", { className: "text-xs", children: [_jsx("b", { children: "Missing:" }), " ", missingLabel(u.missing_fields)] }), _jsxs("p", { className: "text-xs text-muted [overflow-wrap:anywhere]", children: ["File: ", u.filename, " \u00B7 Channel: ", u.channel_title || 'Channel', u.message_id ? ` · Message ID ${u.message_id}` : '', current ? ` · Current: ${current}` : ''] }), u.message_id ? _jsxs("div", { children: [_jsx("p", { className: "mb-1 text-[11px] text-muted", children: "Video snapshots \u00B7 4 of 36" }), _jsx(Frames, { cid: String(u.channel_id ?? ''), mid: Number(u.message_id), title: title })] }) : null] }), _jsx("div", { children: _jsx("button", { type: "button", className: PRIMARY, onClick: onEdit, "aria-label": `Edit video ${title}`, children: "Edit video" }) })] }));
}
/** Open review: ranked suggestions to approve or reject, and videos that still need metadata. */
export function SceneMatchReview({ onClose }) {
    const qc = useQueryClient();
    const [filters, setFilters] = useState(NO_FILTERS);
    const [picked, setPicked] = useState(new Set());
    const [busy, setBusy] = useState(false);
    const [rows, setRows] = useState(new Set());
    const [threshold, setThreshold] = useState(String(DEFAULT_THRESHOLD));
    const [ask, setAsk] = useState(null);
    const [editing, setEditing] = useState(null);
    const q = useInfiniteQuery({
        queryKey: ['scene-review', filters],
        initialPageParam: { so: 0, uo: 0 },
        queryFn: async ({ pageParam }) => { const r = await api(reviewUrl(filters, pageParam.so, pageParam.uo)); if (!r || r.detail)
            throw new ApiError(0, r?.detail || 'Could not load scene-match review.'); return r; },
        getNextPageParam: (_last, all) => { const m = merge(all); return m.hasMore ? nextOffsets(all) : undefined; },
        staleTime: 0,
    });
    const m = merge(q.data?.pages ?? []);
    const quiet = () => { void qc.invalidateQueries({ queryKey: ['scene-review'] }); };
    const lock = busy || rows.size > 0;
    const act = async (s, a) => {
        if (lock)
            return;
        setRows((r) => new Set(r).add(s.suggestion_id));
        try {
            const r = await api(a === 'approve' ? approveUrl(s.suggestion_id) : rejectUrl(s.suggestion_id), { method: 'POST', json: {} });
            if (r?.ok === false)
                throw new ApiError(0, r.detail || oneFailed(a));
            toast(oneDone(a));
            setPicked((p) => { const n = new Set(p); n.delete(s.suggestion_id); return n; });
            quiet();
        }
        catch (e) {
            toast(why(e, oneFailed(a)));
        }
        finally {
            setRows((r) => { const n = new Set(r); n.delete(s.suggestion_id); return n; });
        }
    };
    const rejectSelected = async () => {
        const ids = [...picked];
        if (!ids.length || lock)
            return;
        setBusy(true);
        try {
            const r = await api('/api/metadata/scene-match-review/reject-selected', { method: 'POST', json: { suggestion_ids: ids } });
            if (r?.ok === false)
                throw new ApiError(0, r.detail || 'Selected suggestions could not be rejected.');
            const done = new Set([...(r.rejected_ids ?? []), ...(r.not_pending_ids ?? [])]);
            setPicked((p) => new Set([...p].filter((id) => !done.has(id))));
            toast(rejectedToast(r));
            quiet();
        }
        catch (e) {
            toast(why(e, 'Selected suggestions could not be rejected.'));
        }
        finally {
            setBusy(false);
        }
    };
    const approveAbove = async () => {
        const t = thresholdOf(threshold);
        setAsk(null);
        setBusy(true);
        try {
            const r = await api('/api/metadata/scene-match-review/approve-above', { method: 'POST', json: { threshold: t } });
            if (r?.ok === false)
                throw new ApiError(0, String(r.detail || 'Bulk approval failed.'));
            toast(approvedToast(r));
            quiet();
        }
        catch (e) {
            toast(why(e, 'Bulk approval failed.'));
        }
        finally {
            setBusy(false);
        }
    };
    const toggle = (id) => setPicked((p) => { if (!p.has(id) && p.size >= MAX_SELECTION) {
        toast(selectLimit);
        return p;
    } const n = new Set(p); if (n.has(id))
        n.delete(id);
    else
        n.add(id); return n; });
    const copy = approveAboveCopy(thresholdOf(threshold));
    return (_jsxs(_Fragment, { children: [_jsx(Dialog, { wide: true, open: true, onClose: () => !busy && onClose(), title: "Scene Match Review", actions: _jsx("span", { className: "text-xs text-muted", "aria-live": "polite", children: q.isPending ? 'Loading…' : q.isError ? 'Load failed' : countText(m.total, m.unresolvedTotal, isFiltered(filters)) }), children: _jsxs("div", { className: "grid gap-4 text-left", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 rounded-xl border border-line p-3", children: [_jsx("label", { htmlFor: "sm-threshold", className: "text-sm text-muted", children: "Bulk approve \u2265" }), _jsx("input", { id: "sm-threshold", type: "number", min: 0, max: 100, step: 1, value: threshold, onChange: (e) => setThreshold(e.target.value), "aria-label": "Scene match approval confidence threshold", className: "h-9 w-20 rounded-lg border border-line-2 bg-bg px-2 text-sm" }), _jsx("span", { className: "text-sm text-muted", children: "%" }), _jsx("button", { type: "button", className: BTN, disabled: lock, onClick: () => setAsk(thresholdOf(threshold)), children: "Approve above" }), _jsx("small", { className: "text-xs text-muted", title: "Bulk approval skips suggestions with unresolved cross-provider identity conflicts.", children: "Unresolved conflicts are skipped" })] }), _jsxs("div", { className: "flex flex-wrap items-end gap-3 rounded-xl border border-line p-3", children: [_jsxs("div", { className: "min-w-[10rem]", children: [_jsx("b", { className: "text-sm", children: "Metadata list" }), _jsx("br", {}), _jsx("small", { className: "text-xs text-muted", children: "Filters only this list" })] }), _jsxs("label", { className: "grid gap-1 text-xs text-muted", children: ["Channel", _jsxs("select", { value: filters.channel, onChange: (e) => setFilters({ ...filters, channel: e.target.value }), "aria-label": "Filter videos by channel", className: "h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm text-fg", children: [_jsx("option", { value: "", children: "All channels" }), m.channels.map((c) => _jsx("option", { value: String(c.channel_id), children: c.title || String(c.channel_id) }, String(c.channel_id)))] })] }), _jsxs("label", { className: "grid gap-1 text-xs text-muted", children: ["Missing field", _jsxs("select", { value: filters.field, onChange: (e) => setFilters({ ...filters, field: e.target.value }), "aria-label": "Filter videos by missing field", className: "h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm text-fg", children: [_jsx("option", { value: "all", children: "Any missing field" }), _jsx("option", { value: "performer", children: "Performer" }), _jsx("option", { value: "studio", children: "Studio" })] })] }), _jsx("button", { type: "button", className: BTN, disabled: !isFiltered(filters), onClick: () => setFilters(NO_FILTERS), children: "Clear filters" })] }), q.isPending ? _jsx("div", { role: "status", "aria-label": "Loading review", className: "grid gap-3", children: [0, 1].map((i) => _jsx(Skeleton, { className: "h-44 rounded-2xl" }, i)) })
                            : q.isError ? _jsxs("div", { role: "alert", className: "rounded-xl border border-bad/40 p-4 text-sm", children: [_jsx("p", { children: why(q.error, 'Review request failed.') }), _jsx("button", { type: "button", className: cn(PRIMARY, 'mt-2'), onClick: () => void q.refetch(), children: "Retry" })] })
                                : (_jsxs(_Fragment, { children: [_jsxs("section", { "aria-labelledby": "sm-s", className: "grid gap-2", children: [_jsxs("div", { className: "flex items-baseline justify-between gap-2", children: [_jsx("h3", { id: "sm-s", className: "text-base font-bold", children: "Ranked scene suggestions" }), _jsx("span", { className: "text-xs text-muted", children: visibleText(m.suggestions.length, m.total) })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs("span", { className: "text-xs text-muted", children: [picked.size, " selected"] }), _jsx("button", { type: "button", className: "h-9 rounded-xl border border-bad px-3 text-sm font-semibold text-bad disabled:opacity-50", disabled: !picked.size || lock, onClick: () => void rejectSelected(), children: busy ? 'Working…' : `Reject selected${picked.size ? ` (${picked.size})` : ''}` }), _jsx("button", { type: "button", className: BTN, disabled: !picked.size || lock, onClick: () => setPicked(new Set()), children: "Clear" })] }), m.suggestions.length ? m.suggestions.map((s) => _jsx(SuggestionCard, { s: s, picked: picked.has(s.suggestion_id), busy: lock, onPick: () => toggle(s.suggestion_id), onAct: (a) => void act(s, a) }, s.suggestion_id)) : _jsx("p", { className: "rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted", children: "No pending scene suggestions." })] }), _jsxs("section", { "aria-labelledby": "sm-u", className: "grid gap-2", children: [_jsxs("div", { className: "flex items-baseline justify-between gap-2", children: [_jsx("h3", { id: "sm-u", className: "text-base font-bold", children: "Videos needing metadata" }), _jsx("span", { className: "text-xs text-muted", children: visibleText(m.unresolved.length, m.unresolvedTotal) })] }), m.unresolved.length ? m.unresolved.map((u) => _jsx(UnresolvedCard, { u: u, onEdit: () => setEditing(editItem(u)) }, u.media_id)) : _jsx("p", { className: "rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted", children: "No unresolved catalogue videos." })] }), q.hasNextPage ? _jsx("div", { className: "text-center", children: _jsx("button", { type: "button", className: BTN, disabled: q.isFetchingNextPage, onClick: () => void q.fetchNextPage(), children: q.isFetchingNextPage ? 'Loading…' : 'Load more' }) }) : null] }))] }) }), ask !== null ? _jsx(Confirm, { title: copy.title, text: copy.text, ok: copy.ok, onCancel: () => setAsk(null), onOk: () => void approveAbove() }) : null, editing ? _jsx(QuickEditDialog, { item: editing, onDone: () => { setEditing(null); quiet(); } }, `${editing.id}:${editing._cid}`) : null] }));
}
