import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Confirm } from '@/design/Confirm';
import { Icon } from '@/design/Icon';
import { Skeleton } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { BlurImg } from '@/design/BlurImg';
import { FaceGroups, useFaceGroups } from './FaceGroups';
import { clusterIds, FIND_STEPS, scopeChips, analyzeBody, analyzeCopy, assignBody, bestGuess, findPeopleHint, findStage, groupLabel, jobDone, jobEndText, jobStatusText, peopleSections, rejectBody, suggestionBody, viewFilter } from './faces';
import { usePhotoStore } from './store';
import { FaceReview } from './FaceReview';
import { NamePicker } from './NamePicker';
import { usePhotoChannels } from '@/api/hooks';
const BTN = 'h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-8 rounded-lg bg-accent px-3 text-xs font-bold text-accent-fg disabled:opacity-50';
const sourceOf = (s) => (s.source === 'profile_image' || s.source === 'confirmed_assignment' || s.source === 'metadata' ? s.source : 'metadata');
const why = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : fb);
function Circle({ g, size = 'size-24' }) {
    const [ok, setOk] = useState(true);
    return (_jsx("span", { className: cn('grid shrink-0 place-items-center overflow-hidden rounded-full bg-surface-2 text-2xl font-bold text-muted', size), children: ok && g.representative_url ? _jsx(BlurImg, { src: g.representative_url, alt: "", loading: "lazy", decoding: "async", onError: () => setOk(false), className: "size-full object-cover" }) : _jsx("span", { "aria-hidden": "true", children: "?" }) }));
}
function Card({ g, busy, onOpen, onCheck, onYes, onNo, onName }) {
    const guess = bestGuess(g);
    const [naming, setNaming] = useState(false);
    const named = Boolean(g.performer_id);
    return (_jsxs("article", { "aria-label": groupLabel(g), className: cn('grid shrink-0 content-start justify-items-center gap-2 rounded-2xl border border-line bg-surface p-3 text-center transition-[width] duration-200', naming ? 'w-[280px]' : 'w-[190px]'), children: [_jsx("button", { type: "button", onClick: onOpen, "aria-label": `Show photos of ${groupLabel(g)}`, className: "rounded-full outline-none ring-accent/60 transition hover:scale-[1.03] focus-visible:ring-2", children: _jsx(Circle, { g: g }) }), _jsxs("div", { className: "min-w-0 max-w-full", children: [_jsx("b", { className: "block truncate text-sm", children: named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Who is this?' }), _jsxs("small", { className: "text-xs text-muted", children: [Number(g.photo_count ?? 0).toLocaleString(), " photos"] })] }), named ? _jsx("button", { type: "button", className: BTN, onClick: onCheck, "aria-label": `Check faces of ${groupLabel(g)}`, children: "Check faces" }) : naming ? _jsx(NamePicker, { onPick: (p) => { setNaming(false); onName(p); }, onCancel: () => setNaming(false) }) : guess ? (_jsxs("div", { className: "flex flex-wrap justify-center gap-1.5", children: [_jsx("button", { type: "button", disabled: busy, className: PRIMARY, onClick: () => onYes(guess), "aria-label": `Yes, this is ${guess.name}`, children: "Yes" }), _jsx("button", { type: "button", disabled: busy, className: BTN, onClick: () => onNo(guess), "aria-label": `No, this is not ${guess.name}`, children: "No" }), _jsx("button", { type: "button", className: BTN, onClick: () => setNaming(true), children: "Someone else" })] })) : _jsx("button", { type: "button", className: PRIMARY, onClick: () => setNaming(true), children: "Name" })] }));
}
/** Where "Find people" is: four steps with the current one marked, a bar, and the job's own words. */
function FindProgress({ stage, progress, text }) {
    return (_jsxs("section", { "aria-label": "Finding people", className: "grid gap-3 rounded-2xl border border-line bg-surface p-4", children: [_jsx("ol", { className: "grid gap-2 sm:grid-cols-4", children: FIND_STEPS.map((label, n) => (_jsxs("li", { "aria-current": n === stage ? 'step' : undefined, className: cn('flex items-center gap-2 text-sm', n < stage ? 'text-fg' : n === stage ? 'font-semibold text-fg' : 'text-faint'), children: [_jsx("span", { "aria-hidden": "true", className: cn('grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-bold', n < stage ? 'bg-good text-white' : n === stage ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-muted'), children: n < stage ? '✓' : n + 1 }), label] }, label))) }), _jsx("div", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(progress), "aria-label": "Progress", className: "h-1.5 overflow-hidden rounded-full bg-surface-2", children: _jsx("div", { className: "h-full rounded-full bg-accent transition-[width] duration-500", style: { width: `${Math.max(3, Math.min(100, progress))}%` } }) }), _jsx("p", { className: "text-xs text-muted [overflow-wrap:anywhere]", children: text })] }));
}
/** People inside Photos: faces found in your photos, in plain groups; click a person to see their photos. */
export function PeopleView() {
    const qc = useQueryClient();
    const q = useFaceGroups([]);
    const { setFace, setSelected, setTab } = usePhotoStore();
    const channels = usePhotoChannels();
    const groups = q.data?.groups ?? [];
    const sec = peopleSections(groups);
    const [busy, setBusy] = useState(false);
    const [finding, setFinding] = useState(null);
    const [ask, setAsk] = useState(false);
    const [showRare, setShowRare] = useState(false);
    const [checking, setChecking] = useState(null);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const changed = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['face-groups'] }), qc.invalidateQueries({ queryKey: ['photos'] })]); };
    const open = (g) => { const v = viewFilter(g); if (v.channels.length)
        setSelected(v.channels); setFace(v.filter); setTab('library'); };
    const send = async (g, body, ok) => {
        setBusy(true);
        try {
            const r = await api(`/api/photos/faces/groups/${g.id}/assign`, { method: 'POST', json: body });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || '');
            toast(ok);
            await changed();
        }
        catch (e) {
            toast.error(why(e, 'Could not save the name.'));
        }
        finally {
            setBusy(false);
        }
    };
    const doneText = (pid, nm) => (sec.named.some((g) => Number(g.performer_id) === pid) ? `Added to the existing ${nm}` : `Named ${nm}`);
    const yes = (g, s) => void send(g, suggestionBody(g, s, sourceOf(s), s.basis ?? ''), doneText(s.id, s.name ?? ''));
    const name = (g, p) => void send(g, assignBody(g, p.id), doneText(p.id, p.name));
    const openScope = (c) => { setSelected((channels.data?.channels ?? []).map((ch) => String(ch.telegram_id))); setFace({ performerId: null, groupId: null, groupIds: [], label: c.label, scope: c.scope }); setTab('library'); };
    const no = async (g, s) => {
        setBusy(true);
        try {
            const r = await api('/api/photos/faces/suggestions/reject', { method: 'POST', json: rejectBody(g, s, sourceOf(s)) });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || '');
            await changed();
        }
        catch (e) {
            toast.error(why(e, 'Could not save that.'));
        }
        finally {
            setBusy(false);
        }
    };
    const find = async () => {
        setAsk(false);
        setFinding({ stage: 0, progress: 0, text: 'Starting…' });
        try {
            const r = await api('/api/photos/faces/analyze', { method: 'POST', json: analyzeBody([]) });
            if (!r?.id)
                throw new ApiError(0, r?.detail || 'Could not start.');
            for (let i = 0; i < 7200 && alive.current; i++) {
                await new Promise((res) => setTimeout(res, 2000));
                let s = null;
                try {
                    s = await api(`/api/photos/scan-status/${encodeURIComponent(r.id)}`);
                }
                catch {
                    continue;
                }
                if (!s || !alive.current)
                    continue;
                const status = String(s.status || '').toUpperCase();
                setFinding({ stage: findStage(s.operation, status), progress: Number(s.progress || 0), text: s.operation || jobStatusText(status, s.progress, s.current_item) });
                if (jobDone(status)) {
                    toast(jobEndText(status));
                    await changed();
                    break;
                }
            }
        }
        catch (e) {
            toast.error(why(e, 'Could not start finding people.'));
        }
        finally {
            if (alive.current)
                setFinding(null);
        }
    };
    const grid = (list) => _jsx("div", { className: "flex flex-wrap gap-3", children: list.map((g) => _jsx(Card, { g: g, busy: busy, onOpen: () => open(g), onCheck: () => setChecking(g), onYes: (s) => yes(g, s), onNo: (s) => void no(g, s), onName: (p) => name(g, p) }, g.id)) });
    const C = analyzeCopy(0);
    return (_jsxs("section", { "aria-label": "People", className: "grid gap-6", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsx("p", { role: "status", "aria-live": "polite", className: "max-w-2xl text-sm text-muted", children: finding ? '' : (q.isSuccess ? findPeopleHint(groups, q.data?.analysis) : '') }), _jsxs("button", { type: "button", disabled: !!finding, onClick: () => setAsk(true), className: "inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60", children: [_jsx(Icon, { name: "images", className: "size-4" }), finding ? 'Finding people…' : 'Find people'] })] }), finding ? _jsx(FindProgress, { ...finding }) : null, q.isPending ? _jsx("div", { role: "status", "aria-label": "Loading people", className: "flex flex-wrap gap-3", children: [0, 1, 2, 3].map((i) => _jsxs("div", { className: "grid h-[236px] w-[190px] content-start justify-items-center gap-3 rounded-2xl border border-line bg-surface p-3", children: [_jsx(Skeleton, { className: "size-24 rounded-full" }), _jsx(Skeleton, { className: "h-4 w-28" }), _jsx(Skeleton, { className: "h-3 w-16" }), _jsx(Skeleton, { className: "h-8 w-32 rounded-lg" })] }, i)) })
                : q.isError ? _jsx("button", { type: "button", onClick: () => void q.refetch(), className: cn(PRIMARY, 'w-fit'), children: "Could not load people. Retry" })
                    : (_jsxs(_Fragment, { children: [sec.guesses.length ? _jsxs("section", { "aria-label": "Is this the right person?", className: "grid gap-2", children: [_jsx("h2", { className: "text-lg font-bold", children: "Is this the right person?" }), grid(sec.guesses)] }) : null, sec.named.length ? _jsxs("section", { "aria-label": "Named people", className: "grid gap-2", children: [_jsx("h2", { className: "text-lg font-bold", children: "People" }), grid(sec.named)] }) : null, sec.toName.length ? _jsxs("section", { "aria-label": "Faces to name", className: "grid gap-2", children: [_jsx("h2", { className: "text-lg font-bold", children: "Who are these?" }), grid(sec.toName)] }) : null, sec.rare.length ? (_jsxs("section", { "aria-label": "Other faces", className: "grid gap-2", children: [_jsxs("button", { type: "button", onClick: () => setShowRare(!showRare), "aria-expanded": showRare, className: "w-fit text-sm font-semibold text-accent hover:underline", children: [showRare ? 'Hide' : 'Show', " ", sec.rare.length.toLocaleString(), " faces seen only once or twice"] }), showRare ? grid(sec.rare) : null] })) : null] })), q.isSuccess && scopeChips(q.data?.photo_scopes).length ? (_jsxs("section", { "aria-label": "Photos without a named person", className: "grid gap-2 rounded-2xl border border-line p-4", children: [_jsx("h2", { className: "text-lg font-bold", children: "Photos without a named person" }), _jsx("ul", { className: "grid gap-1.5", children: scopeChips(q.data?.photo_scopes).map((c) => (_jsxs("li", { className: "flex flex-wrap items-center gap-2 text-sm", children: [_jsxs("button", { type: "button", onClick: () => openScope(c), className: "font-semibold text-accent hover:underline", children: [_jsx("span", { className: "tabular-nums", children: c.count.toLocaleString() }), " photos ", c.text] }), _jsx("span", { className: "text-xs text-muted", children: c.hint })] }, c.scope))) })] })) : null, _jsxs("details", { className: "rounded-2xl border border-line p-3", children: [_jsx("summary", { className: "cursor-pointer text-sm font-bold", children: "Advanced: merge, split and review groups" }), _jsx("div", { className: "mt-3", children: _jsx(FaceGroups, { onView: () => setTab('library') }) })] }), checking ? _jsx(FaceReview, { performerId: checking.performer_id ? Number(checking.performer_id) : null, groupIds: clusterIds(checking), label: groupLabel(checking), onClose: () => setChecking(null) }) : null, ask ? _jsx(Confirm, { title: C.title, text: C.text, ok: C.ok, onCancel: () => setAsk(false), onOk: () => void find() }) : null] }));
}
