import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { formatDuration } from '@/features/library/format';
import { useDebounced } from '@/features/library/useDebounced';
import { LOOKUP_FAILED, LOOKUP_IDLE, LOOKUP_NEEDS_INPUT, LOOKUP_SEARCHING, applyMatch, candidateView, hintChips, initialPeople, initialStudio, lookupAllowed, lookupBody, lookupStatus, originLabel, pickPerformer, pickStudio, safeHttpUrl, samePerformer, saveBody, saveOutcome, searchItems, stagedItems } from './quickedit';
const INPUT = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60';
const PANE = 'grid min-w-0 content-start gap-2 rounded-2xl border border-line p-3';
const asList = (v) => (Array.isArray(v) ? v : []);
/** One searchable list (performers or studios): local rows, provider rows and "create new". */
function Picker({ kind, q, onQ, items, loading, onPick }) {
    return (_jsxs(_Fragment, { children: [_jsx("input", { value: q, onChange: (e) => onQ(e.target.value), placeholder: `Search ${kind}…`, "aria-label": `Search ${kind}`, autoComplete: "off", className: INPUT }), _jsx("div", { role: "listbox", "aria-label": `${kind === 'performer' ? 'Performer' : 'Studio'} results`, className: "max-h-56 overflow-y-auto rounded-xl border border-line", children: loading ? _jsx("p", { role: "status", className: "p-3 text-sm text-muted", children: "Searching\u2026" })
                    : items.length ? items.map((x, i) => (_jsxs("button", { type: "button", role: "option", "aria-selected": Boolean(x.selected), onClick: () => onPick(i), className: cn('flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-fg/5', x.selected && 'bg-accent/15'), children: [_jsx("span", { className: "grid size-8 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-bold text-muted", "aria-hidden": "true", children: x.create ? _jsx(Icon, { name: "plus", className: "size-3.5" }) : (x.name.trim().charAt(0).toUpperCase() || '?') }), _jsxs("span", { className: "min-w-0 flex-1", children: [_jsx("span", { className: "block truncate text-sm font-semibold", children: x.create ? `Create “${x.name}”` : x.name }), _jsxs("span", { className: "block truncate text-[11px] text-muted", children: [originLabel(x, kind), x.provider_id ? ` · ID ${String(x.provider_id)}` : ''] })] }), _jsx("span", { className: "shrink-0 text-[11px] font-bold text-muted", children: x.create ? '＋ Create' : x.kind === 'provider' ? (x.selected ? 'Added' : 'API') : (x.count ?? 0) })] }, `${x.kind}:${x.provider ?? ''}:${x.provider_id ?? x.id ?? ''}:${x.name}`))) : _jsx("p", { className: "p-3 text-sm text-muted", children: "No matches in local catalogue or configured providers." }) })] }));
}
/** "Edit video": title, performers, studio and a provider video-data lookup. Nothing is saved until Save changes. */
export function QuickEditDialog({ item, onDone }) {
    const qc = useQueryClient();
    const [title, setTitle] = useState(item.name);
    const [performers, setPerformers] = useState(() => initialPeople(item.performers));
    const [studio, setStudio] = useState(() => initialStudio(item));
    const [performerPicked, setPerformerPicked] = useState(false);
    const [studioPicked, setStudioPicked] = useState(false);
    const [pq, setPq] = useState('');
    const [sq, setSq] = useState('');
    const [matches, setMatches] = useState([]);
    const [hints, setHints] = useState([]);
    const [status, setStatus] = useState('Loading current metadata…');
    const [note, setNote] = useState('');
    const [looking, setLooking] = useState(false);
    const [saving, setSaving] = useState(false);
    const lookupSeq = useRef(0);
    // ---- searches (local + provider), debounced
    const pTerm = useDebounced(pq.trim(), 140), sTerm = useDebounced(sq.trim(), 140);
    const pLocal = useQuery({ queryKey: ['qe-performers', pTerm], queryFn: () => api(`/api/performers?limit=18&q=${encodeURIComponent(pTerm)}`).catch(() => []), enabled: pTerm !== '', staleTime: 10_000 });
    const pProv = useQuery({ queryKey: ['qe-performers-prov', pTerm], queryFn: () => api(`/api/performers/provider-search?limit=8&q=${encodeURIComponent(pTerm)}`).catch(() => []), enabled: pTerm.length >= 2, staleTime: 30_000 });
    const sLocal = useQuery({ queryKey: ['qe-studios', sTerm], queryFn: () => api(`/api/studios?limit=18&q=${encodeURIComponent(sTerm)}`).catch(() => []), enabled: sTerm !== '', staleTime: 10_000 });
    const sProv = useQuery({ queryKey: ['qe-studios-prov', sTerm], queryFn: () => api(`/api/studios/provider-search?limit=8&q=${encodeURIComponent(sTerm)}`).catch(() => []), enabled: sTerm.length >= 2, staleTime: 30_000 });
    const staged = { performers, studio };
    const pItems = pq.trim() ? (pTerm === pq.trim() ? searchItems(pTerm, asList(pLocal.data), asList(pProv.data), staged).map((x) => ({ ...x, selected: x.create ? false : performers.some((p) => samePerformer(p, x)) })) : []) : stagedItems(performers, studio, 'performer');
    const sItems = sq.trim() ? (sTerm === sq.trim() ? searchItems(sTerm, asList(sLocal.data), asList(sProv.data), staged) : []) : stagedItems(performers, studio, 'studio');
    const pLoading = pq.trim() !== '' && (pTerm !== pq.trim() || pLocal.isFetching || (pTerm.length >= 2 && pProv.isFetching));
    const sLoading = sq.trim() !== '' && (sTerm !== sq.trim() || sLocal.isFetching || (sTerm.length >= 2 && sProv.isFetching));
    // ---- provider video-data lookup
    const lookup = async () => {
        const t = title.trim();
        const performer = pq.trim() || String(performers[0]?.name ?? '').trim();
        const studioName = sq.trim() || String(studio?.name ?? '').trim();
        if (!lookupAllowed(t, performer, studioName)) {
            setMatches([]);
            setStatus(LOOKUP_NEEDS_INPUT);
            return;
        }
        const seq = ++lookupSeq.current;
        setLooking(true);
        setStatus(LOOKUP_SEARCHING);
        try {
            const r = await api('/api/media/quick-edit/lookup', { method: 'POST', json: lookupBody({ cid: item._cid, mid: item.id, title: t, performerQuery: pq.trim(), studioQuery: sq.trim(), performers, studio, performerPicked, studioPicked }) });
            if (seq !== lookupSeq.current)
                return;
            if (!r?.ok) {
                setStatus(r?.detail || 'Video-data lookup failed. Check provider configuration and try again.');
                return;
            }
            setMatches(r.matches ?? []);
            setHints(hintChips(r.filename_hints));
            setStatus(lookupStatus(r));
        }
        catch {
            if (seq === lookupSeq.current)
                setStatus(LOOKUP_FAILED);
        }
        finally {
            if (seq === lookupSeq.current)
                setLooking(false);
        }
    };
    const lookupRef = useRef(lookup);
    lookupRef.current = lookup;
    const first = useRef(true);
    // Looks up on open and again shortly after the title, the staged people/studio or a search box changes.
    useEffect(() => {
        const t = setTimeout(() => void lookupRef.current(), first.current ? 0 : 500);
        first.current = false;
        return () => clearTimeout(t);
    }, [title, pq, sq, performers, studio]);
    useEffect(() => () => { lookupSeq.current += 1; }, []);
    const pick = (kind, i) => {
        const x = (kind === 'performer' ? pItems : sItems)[i];
        if (!x)
            return;
        if (kind === 'performer') {
            setPerformers((cur) => pickPerformer(cur, x));
            setPerformerPicked(true);
            setPq('');
        }
        else {
            setStudio(pickStudio(x));
            setStudioPicked(true);
            setSq('');
        }
    };
    const apply = (m) => {
        const a = applyMatch(m);
        if (a.title)
            setTitle(a.title);
        setPerformers(a.performers);
        if (a.studio)
            setStudio(a.studio);
        setNote(a.message);
    };
    const save = async () => {
        setSaving(true);
        try {
            const r = await api('/api/media/quick-edit', { method: 'POST', json: saveBody(item._cid, item.id, title, performers, studio) });
            if (!r?.ok) {
                toast.error(r?.detail || 'Video changes could not be saved.');
                return;
            }
            void qc.invalidateQueries({ queryKey: ['library'] });
            void qc.invalidateQueries({ queryKey: ['video'] });
            void qc.invalidateQueries({ queryKey: ['entities'] });
            const out = saveOutcome(r.caption_sync);
            toast(out.toast);
            if (out.close)
                onDone();
            else
                setStatus(out.status);
        }
        catch (e) {
            toast.error(e instanceof ApiError && e.detail ? e.detail : 'Save failed. Check the connection and try again.');
        }
        finally {
            setSaving(false);
        }
    };
    const summary = [item.filename || item.name || 'Video', item.duration ? formatDuration(item.duration) : ''].filter(Boolean).join(' · ');
    return (_jsx(Dialog, { wide: true, open: true, onClose: () => !saving && onDone(), title: "Edit video", footer: _jsxs("div", { className: "flex flex-wrap items-center justify-end gap-3 border-t border-line pt-3", children: [_jsx("span", { className: "mr-auto text-[11px] text-muted", children: "Saving also syncs the Telegram caption." }), _jsx("button", { type: "button", disabled: saving, onClick: onDone, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", disabled: saving, onClick: () => void save(), className: "h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60", children: saving ? 'Saving & syncing…' : 'Save changes' })] }), children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-3", children: [_jsx("p", { className: "text-xs text-muted", children: summary }), _jsxs("section", { className: PANE, "aria-label": "Title", children: [_jsx("label", { htmlFor: "qe-title", className: "text-xs font-semibold text-muted", children: "Title" }), _jsxs("div", { className: "flex gap-2", children: [_jsx("input", { id: "qe-title", value: title, onChange: (e) => setTitle(e.target.value), placeholder: "Video title", className: INPUT }), _jsxs("button", { type: "button", disabled: looking, onClick: () => void lookup(), "aria-label": "Find video data", title: "Find video data", className: "inline-flex h-11 shrink-0 items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60", children: [_jsx(Icon, { name: "search", className: "size-4" }), _jsx("span", { className: "hidden sm:inline", children: looking ? 'Searching…' : 'Find video data' })] })] }), note ? _jsx("p", { role: "status", className: "rounded-lg bg-accent/10 px-3 py-2 text-xs text-accent", children: note }) : null, hints.length ? _jsx("div", { "aria-label": "Filename clues", className: "flex flex-wrap gap-1.5", children: hints.map((h) => _jsx("span", { className: cn('rounded-full border px-2.5 py-0.5 text-[11px] font-semibold', h.kind === 'unverified' ? 'border-dashed border-line-2 text-muted' : h.kind === 'date' ? 'border-line-2 text-muted' : 'border-accent/40 text-accent'), children: h.label }, `${h.kind}:${h.label}`)) }) : null] }), _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-3", children: [_jsxs("section", { className: PANE, "aria-label": "Performer", children: [_jsx("h3", { className: "text-sm font-bold", children: "Performer" }), _jsx("div", { role: "group", "aria-label": "Selected performers", className: "flex min-h-9 flex-wrap gap-1.5", children: performers.length ? performers.map((p, i) => (_jsxs("span", { className: "inline-flex max-w-full items-center gap-1 rounded-full border border-line-2 bg-bg py-0.5 pl-2.5 pr-1 text-sm font-semibold", children: [_jsx("span", { className: "truncate", children: p.name }), _jsx("button", { type: "button", "aria-label": `Remove ${p.name}`, onClick: () => setPerformers((cur) => cur.filter((_, j) => j !== i)), className: "grid size-6 place-items-center rounded-full text-muted hover:bg-fg/10 hover:text-fg", children: _jsx(Icon, { name: "close", className: "size-3.5" }) })] }, `${p.name}:${i}`))) : _jsx("span", { className: "text-sm text-muted", children: "No performers selected" }) }), _jsx(Picker, { kind: "performer", q: pq, onQ: setPq, items: pItems, loading: pLoading, onPick: (i) => pick('performer', i) })] }), _jsxs("section", { className: PANE, "aria-label": "Studio", children: [_jsx("h3", { className: "text-sm font-bold", children: "Studio" }), _jsx("div", { role: "group", "aria-label": "Selected studio", className: "min-h-9", children: studio ? (_jsxs("span", { className: "inline-flex max-w-full items-center gap-1 rounded-full border border-line-2 bg-bg py-0.5 pl-2.5 pr-1 text-sm font-semibold", children: [_jsx("span", { className: "truncate", children: studio.name }), _jsx("button", { type: "button", "aria-label": "Remove studio", onClick: () => setStudio(null), className: "grid size-6 place-items-center rounded-full text-muted hover:bg-fg/10 hover:text-fg", children: _jsx(Icon, { name: "close", className: "size-3.5" }) })] })) : _jsx("span", { className: "text-sm text-muted", children: "No studio selected" }) }), _jsx(Picker, { kind: "studio", q: sq, onQ: setSq, items: sItems, loading: sLoading, onPick: (i) => pick('studio', i) })] }), _jsxs("section", { className: PANE, "aria-label": "Matched metadata", children: [_jsx("h3", { className: "text-sm font-bold", children: "Matched metadata" }), _jsx("div", { className: "grid max-h-72 gap-2 overflow-y-auto", children: matches.length ? matches.map((m, i) => {
                                        const v = candidateView(m);
                                        return (_jsxs("article", { className: "grid gap-2 rounded-xl border border-line p-2.5", children: [_jsxs("div", { className: "flex gap-2.5", children: [safeHttpUrl(v.thumb) ? _jsx("img", { src: v.thumb, alt: "", loading: "lazy", className: "h-12 w-20 shrink-0 rounded-lg bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }) : _jsx("span", { className: "h-12 w-20 shrink-0 rounded-lg bg-surface-2", "aria-hidden": "true" }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsx("p", { className: "truncate text-sm font-bold", title: v.title, children: v.title }), _jsx("p", { className: "truncate text-[11px] text-muted", children: v.detail }), _jsx("p", { className: "truncate text-[11px] text-muted", children: v.cast })] }), _jsx("span", { className: cn('h-fit shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold', v.tone === 'good' ? 'bg-good/15 text-good' : v.tone === 'warn' ? 'bg-warn/15 text-warn' : v.tone === 'bad' ? 'bg-bad/15 text-bad' : 'bg-surface-3 text-muted'), children: v.confidence })] }), _jsx("button", { type: "button", onClick: () => apply(m), className: "h-9 rounded-lg border border-line-2 text-sm font-semibold hover:bg-fg/5", "aria-label": `Apply match: ${v.title}`, children: "Apply" })] }, i));
                                    }) : _jsx("p", { className: "text-sm text-muted", children: "No matching provider video data." }) }), _jsx("p", { role: "status", "aria-live": "polite", className: "text-[11px] text-muted", children: status === 'Loading current metadata…' && !looking ? LOOKUP_IDLE : status })] })] })] }) }));
}
