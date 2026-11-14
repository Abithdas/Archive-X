import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useSuggested } from '@/api/hooks';
import { Icon } from '@/design/Icon';
import { SuggestionPreview } from './SuggestionPreview';
import { PosterGrid, StudioPoster } from './PosterCard';
import { cn } from '@/design/cn';
import { formatBytes, formatDuration } from '@/features/library/format';
import { SIMILAR_EMPTY, SUGGEST_EMPTY, brandIcon, confirmBody, confirmPath, failedConfirm, failedReject, linkedText, pending, rejectPath, rejectedText, succeeded, suggestionQuestion, suggestionTitle, suggestionToolbar, yesLabel } from './studioextras';
import { Empty } from '@/design/Empty';
export function SocialPanel({ links, loading, label = 'Website and social links' }) {
    return (_jsx("div", { "aria-label": label, className: "mt-[9px] flex min-h-9 flex-wrap items-center gap-2", children: links.length ? links.map((l) => (_jsx("a", { href: l.href, target: "_blank", rel: "noopener noreferrer", "aria-label": `Open ${l.label}`, title: l.label, className: "grid size-9 place-items-center rounded-full border border-line bg-surface-2 text-muted hover:bg-fg/5 hover:text-fg", children: _jsx(Icon, { name: brandIcon(l.href), className: "size-[18px]" }) }, l.href))) : _jsx("span", { className: "py-1.5 text-[11.5px] leading-[1.4] text-faint", children: loading ? 'Checking saved profile links…' : 'No website or social links saved.' }) }));
}
export function DetailsCard({ rows, related, label = 'Studio details' }) {
    return (_jsxs("section", { "aria-label": label, className: "mb-4 rounded-[18px] border border-line bg-surface p-3.5", children: [_jsxs("div", { className: "flex items-baseline justify-between gap-3", children: [_jsx("h2", { className: "text-sm font-bold tracking-[-0.01em]", children: "Details" }), _jsx("span", { className: "text-[11px] text-faint", children: "From saved profile & providers" })] }), _jsxs("dl", { className: "mt-[9px] grid grid-cols-2 gap-1.5 sm:grid-cols-[repeat(auto-fit,minmax(min(140px,100%),1fr))] sm:gap-2", children: [rows.map(([k, v]) => _jsxs("div", { className: "flex min-h-[54px] min-w-0 flex-col items-start gap-1 rounded-[10px] border border-line bg-bg px-2.5 py-[9px] text-left", children: [_jsx("dt", { className: "text-[10.5px] font-medium leading-[1.3] text-faint", children: k }), _jsx("dd", { className: "text-xs font-semibold leading-[1.35] [overflow-wrap:anywhere]", children: v })] }, k)), related.length ? (_jsxs("div", { className: "col-span-full min-w-0 text-left", children: [_jsx("dt", { className: "text-[10.5px] font-medium text-faint", children: "Related studios" }), _jsx("dd", { className: "mt-1 flex flex-wrap gap-2", children: related.map((r) => _jsxs(Link, { to: "/studios/$id", params: { id: String(r.id) }, title: r.relation || 'Related studio', className: "rounded-[10px] border border-line px-3 py-1.5 text-xs font-semibold hover:bg-fg/5", children: [r.name, _jsx("small", { className: "ml-1.5 text-[11px] font-normal text-muted", children: r.relation || 'Related' })] }, r.id)) })] })) : null] })] }));
}
export function SimilarPanel({ items }) {
    if (!items.length)
        return _jsx(Empty, { role: "tabpanel", "aria-label": "Similar Studios", icon: "studio", children: SIMILAR_EMPTY });
    return _jsx(PosterGrid, { label: "Similar Studios", children: items.map((s) => _jsx(StudioPoster, { id: s.id, name: s.name, image: s.image, count: s.count }, s.id)) });
}
function SuggestionCard({ s, name, kind, busy, onConfirm, onReject, onPreview }) {
    const [thumb, setThumb] = useState(true);
    const meta = [s.duration ? formatDuration(s.duration) : '', s.size ? formatBytes(s.size) : ''].filter(Boolean).join(' · ');
    return (_jsxs("article", { "aria-label": suggestionTitle(s), className: "overflow-hidden rounded-card border border-line bg-surface", children: [_jsx("button", { type: "button", onClick: onPreview, "aria-label": `Check frames: ${suggestionTitle(s)}`, title: "Check four frames of this video", className: "relative block aspect-video w-full cursor-pointer bg-surface-3 outline-none focus-visible:ring-2 focus-visible:ring-accent", children: thumb ? _jsx("img", { src: `/api/thumb/${encodeURIComponent(String(s.channel_id))}/${s.message_id}?r=f3`, alt: "", loading: "lazy", decoding: "async", className: "size-full object-cover", onError: () => setThumb(false) }) : _jsx("span", { className: "grid size-full place-items-center text-muted", children: _jsx(Icon, { name: "play", className: "size-8" }) }) }), _jsxs("div", { className: "grid gap-1.5 p-3 text-left", children: [_jsx("b", { className: "line-clamp-2 text-sm [overflow-wrap:anywhere]", children: suggestionTitle(s) }), meta ? _jsx("span", { className: "text-xs text-muted", children: meta }) : null, _jsx("span", { className: "text-xs text-muted", children: suggestionQuestion(name) }), _jsxs("div", { className: "mt-1 flex flex-wrap gap-2", children: [_jsx("button", { type: "button", disabled: busy, onClick: onConfirm, className: "h-9 rounded-xl bg-accent px-3 text-xs font-bold text-accent-fg disabled:opacity-60", "aria-label": `${yesLabel(kind)}: ${suggestionTitle(s)}`, children: yesLabel(kind) }), _jsx("button", { type: "button", disabled: busy, onClick: onReject, className: "h-9 rounded-xl border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-60", "aria-label": `No, don’t link: ${suggestionTitle(s)}`, children: "No, don\u2019t link" })] })] })] }));
}
/** Videos the scan thinks belong to this studio: link each one, or say it does not. */
export function SuggestionsPanel({ kind = 'studio', entityId: studioId, name }) {
    const q = useSuggested(kind, studioId);
    const qc = useQueryClient();
    const [busy, setBusy] = useState(new Set());
    const [previewId, setPreviewId] = useState(null);
    const act = async (s, act_) => {
        if (busy.has(s.suggestion_id))
            return;
        setBusy((b) => new Set(b).add(s.suggestion_id));
        const fail = act_ === 'confirm' ? failedConfirm : failedReject;
        try {
            const r = await api(act_ === 'confirm' ? confirmPath(s.suggestion_id) : rejectPath(s.suggestion_id), { method: 'POST', json: confirmBody(studioId, kind) });
            if (!succeeded(r))
                throw new ApiError(0, r?.detail || r?.message || (act_ === 'confirm' ? 'The server did not confirm this suggestion.' : `The server did not dismiss this ${kind} link.`));
            toast(act_ === 'confirm' ? linkedText(kind) : rejectedText(kind));
            await Promise.all([q.refetch(), qc.invalidateQueries({ queryKey: [kind, studioId] })]);
        }
        catch (e) {
            toast.error(fail(e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e)));
        }
        finally {
            setBusy((b) => { const n = new Set(b); n.delete(s.suggestion_id); return n; });
        }
    };
    const list = q.data ?? [];
    const shown = list.find((x) => x.suggestion_id === previewId) ?? null;
    if (q.isPending)
        return _jsx("p", { role: "tabpanel", "aria-label": "Suggestions", className: "py-8 text-center text-sm text-muted", children: "Loading suggestions\u2026" });
    if (!list.length)
        return _jsxs("div", { role: "tabpanel", "aria-label": "Suggestions", className: "rounded-card border border-dashed border-line py-12 text-center", children: [_jsx("b", { children: SUGGEST_EMPTY.title }), _jsx("p", { className: "mt-1 text-sm text-muted", children: SUGGEST_EMPTY.body })] });
    return (_jsxs("div", { role: "tabpanel", "aria-label": "Suggestions", className: "grid gap-4", children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0 max-w-3xl text-left", children: [_jsx("b", { children: "Suggested Videos" }), _jsx("p", { className: "mt-1 text-xs text-muted", children: suggestionToolbar(name, kind) })] }), _jsx("span", { className: "rounded-full bg-surface-2 px-2.5 py-1 text-xs font-semibold", children: pending(list.length) })] }), _jsx("div", { className: cn('grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4'), children: list.map((s) => _jsx(SuggestionCard, { s: s, name: name, kind: kind, busy: busy.has(s.suggestion_id), onConfirm: () => void act(s, 'confirm'), onReject: () => void act(s, 'reject'), onPreview: () => setPreviewId(s.suggestion_id) }, s.suggestion_id)) }), shown ? _jsx(SuggestionPreview, { s: shown, name: name, kind: kind, busy: busy.has(shown.suggestion_id), onConfirm: () => void act(shown, 'confirm'), onReject: () => void act(shown, 'reject'), onClose: () => setPreviewId(null) }) : null] }));
}
