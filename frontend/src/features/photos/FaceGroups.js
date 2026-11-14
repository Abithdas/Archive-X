import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
import { Confirm } from '@/design/Confirm';
import { Icon } from '@/design/Icon';
import { Skeleton } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { EMPTY_BODY, EMPTY_TITLE, INTRO, LOAD_FAILED, MERGE_NEEDS_TWO, NEEDS_PERFORMER, CLEARED, REJECTED, analysisStatus, analyzeBody, analyzeCopy, assignBody, assignedStatus, assignedText, confirmedText, countsLine, firstDetection, groupLabel, isAssigned, jobDone, jobEndText, jobStatusText, mergeBody, mergeCopy, mergedText, photosLabel, rejectBody, scoreText, splitCopy, splitText, suggestionBody, suggestionReason, suggestionsFor, UNANALYZED, viewFilter } from './faces';
import { ReferenceReview } from './ReferenceReview';
import { usePhotoStore } from './store';
import { withWidth } from '@/design/responsive';
import { Empty } from '@/design/Empty';
const NO_CHANNELS = [];
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50';
const reason = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb);
function Suggestions({ group, source, items, onCompare, onConfirm, onReject }) {
    if (!items.length)
        return null;
    const title = source === 'metadata' ? 'Suggestions from source text' : source === 'profile_image' ? 'Looks like these performers' : 'Suggestions from your previous confirmations';
    const note = source === 'metadata' ? 'Based on exact filename, caption, or channel-title text matches.' : source === 'profile_image' ? 'The face in this group resembles the performer\'s profile picture. Check it, then confirm; nothing is assigned automatically.' : 'Ranked by similarity to face groups you previously assigned. These are candidates, not automatic assignments.';
    return (_jsxs("div", { className: "mt-2 grid gap-1.5 rounded-xl border border-line bg-bg p-2.5", "aria-label": title, children: [_jsx("strong", { className: "text-xs", children: title }), _jsx("small", { className: "text-[11px] text-muted", children: note }), items.map((s) => (_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("img", { src: s.image_url ?? '', alt: "", loading: "lazy", className: "size-9 rounded-full bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), _jsxs("span", { className: "min-w-0 flex-1 text-left", children: [_jsx("b", { className: "block truncate text-sm", children: s.name || 'Unknown performer' }), _jsx("small", { className: "block text-[11px] text-muted", children: suggestionReason(s, source) }), scoreText(s) ? _jsx("small", { className: "block text-[11px] text-faint", children: scoreText(s) }) : null] }), _jsx("button", { type: "button", className: cn(BTN, 'h-8 px-2.5 text-xs'), onClick: () => onCompare(s), "aria-label": `Compare ${s.name} with ${groupLabel(group)}`, children: "Compare" }), _jsx("button", { type: "button", className: cn(PRIMARY, 'h-8 px-2.5 text-xs'), onClick: () => onConfirm(s, source), "aria-label": `Confirm ${s.name} for ${groupLabel(group)}`, children: "Confirm" }), _jsx("button", { type: "button", className: cn(BTN, 'h-8 px-2.5 text-xs'), onClick: () => onReject(s, source), "aria-label": `Not ${s.name} for ${groupLabel(group)}`, children: "Not this" })] }, `${source}:${s.id}`)))] }));
}
function PerformerSearch({ group, picked, onPick }) {
    const [text, setText] = useState(picked?.name ?? group.performer_name ?? '');
    const [list, setList] = useState(null);
    const [err, setErr] = useState('');
    const timer = useRef(undefined);
    useEffect(() => () => clearTimeout(timer.current), []);
    useEffect(() => { if (picked) {
        setText(picked.name);
        setList(null);
    } }, [picked]);
    const type = (v) => {
        setText(v);
        if (picked && v !== picked.name)
            onPick(null);
        clearTimeout(timer.current);
        if (v.trim().length < 2) {
            setList(null);
            return;
        }
        timer.current = setTimeout(async () => {
            try {
                const r = await api(`/api/photos/faces/performers?limit=12&q=${encodeURIComponent(v.trim())}`);
                setList(Array.isArray(r?.items) ? r.items.slice(0, 12) : []);
                setErr('');
            }
            catch (e) {
                setList([]);
                setErr(reason(e, 'Performer search failed.'));
            }
        }, 220);
    };
    const id = `face-search-${group.id}`;
    return (_jsxs("div", { className: "mt-2 text-left", children: [_jsx("label", { htmlFor: id, className: "text-xs font-semibold text-muted", children: "Assign to performer" }), _jsx("input", { id: id, type: "search", value: text, onChange: (e) => type(e.target.value), placeholder: "Search local performer names\u2026", autoComplete: "off", className: "mt-1 h-10 w-full rounded-xl border border-line-2 bg-surface px-3 text-sm" }), list ? (_jsx("div", { role: "listbox", "aria-label": `Performers for ${groupLabel(group)}`, className: "mt-1 grid max-h-48 overflow-y-auto rounded-xl border border-line bg-surface p-1", children: list.length ? list.map((p) => (_jsxs("button", { type: "button", role: "option", "aria-selected": picked?.id === p.id, onClick: () => { onPick({ id: p.id, name: p.name }); setText(p.name); setList(null); }, className: "flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-fg/5", children: [_jsx("img", { src: withWidth(p.image_url || `/api/performers/${p.id}/image`, 160), alt: "", loading: "lazy", className: "size-7 rounded-full bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), _jsx("span", { className: "flex-1 truncate", children: p.name }), _jsx("small", { className: "text-[11px] text-faint", children: "Local performer" })] }, p.id))) : _jsx("div", { className: "px-2 py-1.5 text-xs text-muted", children: err || 'No local performer matches.' }) })) : null] }));
}
function Card({ group, checked, onCheck, busy, onView, onAssign, onClear, onSplit, onConfirm, onReject }) {
    const [picked, setPicked] = useState(group.performer_id ? { id: Number(group.performer_id), name: group.performer_name ?? '' } : null);
    const [cover, setCover] = useState(true);
    const [reviewing, setReviewing] = useState(false);
    const compare = (s) => { setPicked({ id: s.id, name: s.name ?? '' }); setReviewing(true); };
    const sug = suggestionsFor(group);
    const label = groupLabel(group);
    const det = firstDetection(group);
    return (_jsxs("article", { "aria-label": label, className: "grid content-start gap-2 rounded-2xl border border-line bg-surface p-3", children: [_jsxs("label", { className: "inline-flex items-center gap-2 text-xs font-semibold text-muted", children: [_jsx("input", { type: "checkbox", checked: checked, onChange: onCheck, "aria-label": `Select cluster ${label} to merge` }), "Select cluster to merge"] }), _jsxs("button", { type: "button", onClick: onView, title: `View photos in ${label}`, className: "relative block aspect-square w-full overflow-hidden rounded-xl bg-surface-3", children: [cover ? _jsx("img", { src: group.representative_url ?? '', alt: `Representative face for ${label}`, loading: "lazy", decoding: "async", className: "size-full object-cover", onError: () => setCover(false) }) : _jsx("span", { className: "grid size-full place-items-center text-xs text-muted", children: "No preview" }), _jsx("span", { className: "absolute bottom-2 left-2 rounded-full bg-black/60 px-2 py-0.5 text-xs font-semibold text-white", children: photosLabel(group) })] }), _jsxs("div", { className: "flex items-center gap-2 text-left", children: [group.performer_id && group.performer_image_url ? _jsx("img", { src: `${group.performer_image_url}?v=${encodeURIComponent(group.updated_at ?? '')}`, alt: "", loading: "lazy", className: "size-10 rounded-full bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.display = 'none') }) : null, _jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "truncate text-base font-bold", children: label }), _jsx("p", { className: "text-xs text-muted", children: countsLine(group) })] })] }), _jsx("span", { className: cn('w-fit rounded-full px-2.5 py-0.5 text-xs font-semibold', isAssigned(group) ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn'), children: assignedText(group) }), _jsx(Suggestions, { group: group, source: "profile_image", items: sug.profile, onCompare: compare, onConfirm: onConfirm, onReject: onReject }), _jsx(Suggestions, { group: group, source: "confirmed_assignment", items: sug.confirmed, onCompare: compare, onConfirm: onConfirm, onReject: onReject }), _jsx(Suggestions, { group: group, source: "metadata", items: sug.metadata, onCompare: compare, onConfirm: onConfirm, onReject: onReject }), _jsx(PerformerSearch, { group: group, picked: picked, onPick: setPicked }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsx("button", { type: "button", className: BTN, onClick: () => setReviewing(true), title: "Open an unblurred side-by-side review; choose a performer here if one is not selected", "aria-label": `Review profile/provider images for ${label}`, children: "Review profile/provider images" }), _jsx("button", { type: "button", disabled: busy, className: PRIMARY, onClick: () => void onAssign(picked), "aria-label": `Save assignment for ${label}`, children: "Save assignment" }), _jsx("button", { type: "button", disabled: busy, className: BTN, onClick: () => { setPicked(null); onClear(); }, "aria-label": `Clear assignment for ${label}`, children: "Clear" }), _jsx("button", { type: "button", className: BTN, onClick: onView, "aria-label": `View photos in ${label}`, children: "View photos" }), det ? _jsx("button", { type: "button", disabled: busy, className: BTN, onClick: () => onSplit(det), title: "Move one face detection to a new cluster", "aria-label": `Separate one face from ${label}`, children: "Separate one face" }) : null] }), reviewing ? _jsx(ReferenceReview, { group: group, picked: picked, onPick: setPicked, onSave: async () => (picked ? Boolean(await onAssign(picked)) : false), onClose: () => setReviewing(false) }) : null] }));
}
/** The groups address. `suggestions` false leaves out the work of guessing who each group might be (seconds on a big library); the Photos page only needs the people themselves. */
export const faceGroupsUrl = (selected, suggestions = true, top = 0) => {
    const p = new URLSearchParams();
    if (selected.length)
        p.set('channels', selected.join(','));
    if (!suggestions)
        p.set('suggestions', '0');
    if (top)
        p.set('top', String(top));
    const q = p.toString();
    return `/api/photos/faces/groups${q ? `?${q}` : ''}`;
};
export function useFaceGroups(selected, enabled = true, suggestions = true, top = 0) {
    return useQuery({
        queryKey: ['face-groups', selected, suggestions, top],
        enabled,
        queryFn: async () => { const r = await api(faceGroupsUrl(selected, suggestions, top)); if (!r || r.ok !== true)
            throw new ApiError(0, r?.detail || LOAD_FAILED); return r; },
        staleTime: 0,
    });
}
/** The Image Analysis tab: anonymous face groups, with analysis, merge, split, and manual or suggested performer assignment. */
export function FaceGroups({ onView }) {
    const { setSelected, setFace } = usePhotoStore();
    const selected = NO_CHANNELS; // People covers the whole photo library, whatever channels Photos has selected
    const qc = useQueryClient();
    const q = useFaceGroups(selected);
    const groups = q.data?.groups ?? [];
    const [note, setNote] = useState(null);
    const [clusters, setClusters] = useState(new Set());
    const [watching, setWatching] = useState(false);
    const [busy, setBusy] = useState(false);
    const [ask, setAsk] = useState(null);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const say = (text, tone = '') => setNote({ text, tone });
    const refresh = useCallback(async () => { await q.refetch(); }, [q]);
    const changed = async () => { void qc.invalidateQueries({ queryKey: ['photos'] }); await refresh(); };
    const st = q.data ? analysisStatus(groups, q.data.analysis ?? {}, selected.length, watching) : null;
    const shown = note ?? (st ? { text: st.text, tone: st.tone } : { text: UNANALYZED, tone: '' });
    const analyze = () => {
        const c = analyzeCopy(selected.length);
        setAsk({ title: c.title, text: c.text, ok: c.ok, run: async () => {
                say('Queueing image-based face analysis…');
                try {
                    const r = await api('/api/photos/faces/analyze', { method: 'POST', json: analyzeBody(selected) });
                    if (!r?.id)
                        throw new ApiError(0, r?.detail || 'Could not queue face analysis.');
                    say(`Face analysis queued as job ${r.id}. View progress in Operations.`, 'success');
                    setWatching(true);
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
                        say(jobStatusText(status, s.progress, s.current_item));
                        if (jobDone(status)) {
                            const ok = status === 'COMPLETED' || status === 'COMPLETED_WITH_ERRORS';
                            say(jobEndText(status), ok ? 'success' : 'error');
                            setNote(null);
                            await changed();
                            void qc.invalidateQueries({ queryKey: ['photo-channels'] });
                            break;
                        }
                    }
                }
                catch (e) {
                    say(reason(e, 'Could not start face analysis.'), 'error');
                }
                finally {
                    if (alive.current)
                        setWatching(false);
                }
            } });
    };
    const merge = () => {
        const ids = [...clusters];
        if (ids.length < 2)
            return void say(MERGE_NEEDS_TWO, 'error');
        const c = mergeCopy(ids.length);
        setAsk({ title: c.title, text: c.text, ok: c.ok, run: async () => {
                setBusy(true);
                try {
                    const r = await api('/api/photos/faces/groups/merge', { method: 'POST', json: mergeBody(ids) });
                    if (!r || r.ok !== true)
                        throw new ApiError(0, r?.detail || 'Could not merge face clusters.');
                    setClusters(new Set());
                    say(mergedText(ids.length, r.target_group_id), 'success');
                    await changed();
                }
                catch (e) {
                    say(reason(e, 'Could not merge face clusters.'), 'error');
                }
                finally {
                    setBusy(false);
                }
            } });
    };
    const split = (detection) => setAsk({ title: splitCopy.title, text: splitCopy.text, ok: splitCopy.ok, run: async () => {
            setBusy(true);
            try {
                const r = await api(`/api/photos/faces/detections/${detection}/split`, { method: 'POST', json: {} });
                if (!r || r.ok !== true)
                    throw new ApiError(0, r?.detail || 'Could not split face detection.');
                setClusters(new Set());
                say(splitText(r.new_group_id), 'success');
                await changed();
            }
            catch (e) {
                say(reason(e, 'Could not split face detection.'), 'error');
            }
            finally {
                setBusy(false);
            }
        } });
    const post = async (g, body, okText, failText) => {
        setBusy(true);
        try {
            const r = await api(`/api/photos/faces/groups/${g.id}/assign`, { method: 'POST', json: body });
            if (!r || r.ok !== true)
                throw new ApiError(0, r?.detail || failText);
            say(okText, 'success');
            await changed();
            return true;
        }
        catch (e) {
            say(reason(e, failText), 'error');
            return false;
        }
        finally {
            setBusy(false);
        }
    };
    const assign = async (g, p) => { if (!p) {
        say(NEEDS_PERFORMER, 'error');
        return false;
    } return post(g, assignBody(g, p.id), assignedStatus(p.name), 'Could not save performer assignment.'); };
    const clear = (g) => void post(g, assignBody(g, null), CLEARED, 'Could not clear performer assignment.');
    const confirmSuggestion = (g, s, src) => void post(g, suggestionBody(g, s, src, s.basis ?? ''), confirmedText(s.name ?? ''), 'Could not confirm this suggestion.');
    const reject = async (g, s, src) => {
        setBusy(true);
        try {
            const r = await api('/api/photos/faces/suggestions/reject', { method: 'POST', json: rejectBody(g, s, src) });
            if (!r || r.ok !== true)
                throw new ApiError(0, r?.detail || 'Could not save this rejection.');
            say(REJECTED, 'success');
            await refresh();
        }
        catch (e) {
            say(reason(e, 'Could not reject this suggestion.'), 'error');
        }
        finally {
            setBusy(false);
        }
    };
    const view = (g) => { const v = viewFilter(g); if (v.channels.length)
        setSelected(v.channels); setFace(v.filter); onView(); };
    const toggle = (id) => setClusters((s) => { const n = new Set(s); if (n.has(id))
        n.delete(id);
    else
        n.add(id); return n; });
    return (_jsxs("section", { "aria-label": "People", className: "grid gap-4", children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsx("div", { className: "min-w-0 max-w-3xl", children: _jsx("p", { className: "text-sm text-muted", children: INTRO }) }), _jsxs("button", { type: "button", disabled: !st || st.analyzeDisabled, onClick: analyze, className: cn(PRIMARY, 'inline-flex h-10 items-center gap-2'), children: [_jsx(Icon, { name: "images", className: "size-4" }), st?.analyzeLabel ?? 'Analyze images for faces'] })] }), _jsx("p", { role: "status", "aria-live": "polite", className: cn('text-sm', shown.tone === 'error' ? 'text-bad' : shown.tone === 'success' ? 'text-good' : 'text-muted'), children: q.isError ? reason(q.error, LOAD_FAILED) : shown.text }), groups.length ? (_jsxs("div", { role: "group", "aria-label": "Face cluster corrections", className: "flex flex-wrap items-center gap-2 rounded-xl border border-line p-2.5", children: [_jsxs("span", { className: "text-sm", children: [clusters.size, " clusters selected"] }), _jsx("button", { type: "button", className: PRIMARY, disabled: busy || clusters.size < 2, onClick: merge, children: "Merge selected clusters" }), _jsx("button", { type: "button", className: BTN, disabled: !clusters.size, onClick: () => setClusters(new Set()), children: "Clear selection" })] })) : null, q.isPending ? _jsx("div", { role: "status", "aria-label": "Loading face groups", className: "grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3", children: [0, 1, 2].map((i) => _jsx(Skeleton, { className: "h-72 rounded-2xl" }, i)) })
                : q.isError ? _jsx("button", { type: "button", onClick: () => void q.refetch(), className: cn(PRIMARY, 'w-fit'), children: "Retry" })
                    : !groups.length ? _jsx(Empty, { icon: "user", title: EMPTY_TITLE, children: EMPTY_BODY })
                        : _jsx("div", { className: "grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3", children: groups.map((g) => _jsx(Card, { group: g, checked: clusters.has(g.id), busy: busy, onCheck: () => toggle(g.id), onView: () => view(g), onAssign: (p) => assign(g, p), onClear: () => clear(g), onSplit: split, onConfirm: (s, src) => confirmSuggestion(g, s, src), onReject: (s, src) => void reject(g, s, src) }, g.id)) }), ask ? _jsx(Confirm, { title: ask.title, text: ask.text, ok: ask.ok, onCancel: () => setAsk(null), onOk: () => { const go = ask.run; setAsk(null); void go(); } }) : null] }));
}
