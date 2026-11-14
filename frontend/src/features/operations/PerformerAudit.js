import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Confirm } from '@/design/Confirm';
import { Dialog } from '@/design/Dialog';
import { cn } from '@/design/cn';
import { AUDITING, AUDIT_FAILED, AUDIT_NOTE, AUTO_FAILED, JUNK_FAILED, MERGE_FAILED, NOTHING_SAFE, NO_GROUPS_NOTE, NO_GROUPS_TITLE, autoConfirm, autoGroups, autoLabel, autoOutcome, autoToast, canMerge, groupFooter, headingFor, identityText, isProtected, junkBody, junkCheck, junkConfirm, junkOutcome, kindInfo, manualConfirm, memberLine, mergeBody, mergeIds, mergedMessage, similarityLabel, targetLine, totalsLine, truncatedNote } from './audit';
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50';
const reason = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb);
/** One group of likely duplicates: choose one to Keep and tick the ones to Merge into it. */
function GroupCard({ group, busy, onMerge }) {
    const [keep, setKeep] = useState(null);
    const [checked, setChecked] = useState(new Set());
    const info = kindInfo(group.kind);
    const locked = isProtected(group);
    const toggle = (id) => setChecked((s) => { const n = new Set(s); if (n.has(id))
        n.delete(id);
    else
        n.add(id); return n; });
    return (_jsxs("section", { className: "rounded-2xl border border-line p-3.5", "aria-label": group.title || info.title, children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("h5", { className: "text-sm font-bold", children: [group.title || info.title, similarityLabel(group)] }), _jsx("p", { className: "mt-1 text-[11px] leading-snug text-muted", children: group.reason || info.note }), _jsxs("p", { className: "mt-1 text-[11px] text-accent", children: ["Recommended treatment: ", group.recommended_treatment || info.treatment] })] }), locked ? null : _jsx("button", { type: "button", disabled: busy || !canMerge(keep, checked), onClick: () => keep !== null && onMerge(group, keep, mergeIds(keep, checked)), className: PRIMARY, children: "Merge checked" })] }), _jsx("ul", { className: "mt-3 grid gap-2", children: (group.performers ?? []).map((p) => (_jsxs("li", { className: "flex flex-wrap items-center justify-between gap-3 rounded-xl bg-bg p-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("b", { className: "block truncate text-sm", children: p.name || 'Unnamed performer' }), _jsx("span", { className: "block text-xs text-muted", children: memberLine(p) }), _jsx("small", { className: "block text-[11px] text-faint [overflow-wrap:anywhere]", children: identityText(p) }), p.aliases?.length ? _jsxs("small", { className: "block text-[11px] text-faint [overflow-wrap:anywhere]", children: ["Aliases: ", p.aliases.join(', ')] }) : null] }), locked ? _jsx("span", { className: "text-xs text-muted", children: "Protected fallback" }) : (_jsxs("div", { className: "flex gap-4 text-sm", children: [_jsxs("label", { className: "inline-flex items-center gap-1.5", children: [_jsx("input", { type: "radio", name: `keep-${group.kind}-${group.performers?.[0]?.id}`, checked: keep === p.id, onChange: () => setKeep(p.id), "aria-label": `Keep ${p.name}` }), "Keep"] }), _jsxs("label", { className: "inline-flex items-center gap-1.5", children: [_jsx("input", { type: "checkbox", checked: checked.has(p.id) && keep !== p.id, disabled: keep === p.id, onChange: () => toggle(p.id), "aria-label": `Merge ${p.name}` }), "Merge"] })] }))] }, p.id))) }), _jsx("p", { className: "mt-2 text-[11px] text-muted", children: groupFooter(group) })] }));
}
/** A record that looks like part of a filename or release title, with a safe suggested target. */
function Fragment({ group, busy, onAct }) {
    const p = (group.performers ?? [])[0];
    if (!p)
        return null;
    const target = group.suggested_target;
    const linked = Number(p.video_count || 0) > 0;
    return (_jsxs("section", { className: "rounded-2xl border border-warn/60 p-3.5", "aria-label": `Fragment: ${p.name}`, children: [_jsxs("h5", { className: "text-sm font-bold", children: ["Likely filename/title fragment: ", p.name || 'Unnamed performer'] }), _jsx("p", { className: "mt-1 text-xs text-muted", children: memberLine(p) }), _jsx("p", { className: "mt-1 text-[11px] text-muted", children: group.reason || 'This performer name resembles a release tag or scene-title fragment.' }), target ? _jsxs("div", { className: "my-2.5 rounded-xl border border-line bg-bg p-2.5", children: [_jsx("small", { className: "text-muted", children: "Suggested merge target" }), _jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("b", { children: target.name || 'Performer' }), _jsx("span", { className: "text-xs text-muted", children: targetLine(target) })] })] })
                : _jsx("p", { className: "my-2.5 text-xs text-muted", children: "No safe suggested target found. Review manually before changing this record." }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [target && target.id !== p.id ? _jsxs("button", { type: "button", disabled: busy, onClick: () => onAct(group, 'merge'), className: PRIMARY, children: ["Merge into ", target.name || 'target'] }) : null, !linked ? _jsx("button", { type: "button", disabled: busy, onClick: () => onAct(group, 'delete'), className: BTN, children: "Delete unlinked record" }) : _jsx("span", { className: "text-[11px] text-muted", children: "Delete disabled \u00B7 merge linked videos first" })] })] }));
}
/** Performer duplicate review. Scanning is read-only; every merge or delete asks first and is a separate, explicit action. */
export function PerformerAudit({ onClose }) {
    const qc = useQueryClient();
    const [data, setData] = useState(null);
    const [status, setStatus] = useState(AUDITING);
    const [outcome, setOutcome] = useState('');
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [ask, setAsk] = useState(null);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const scan = useCallback(async () => {
        setLoading(true);
        setStatus(AUDITING);
        try {
            const r = await api('/api/performers/duplicate-audit');
            if (!alive.current)
                return;
            if (r?.ok !== true)
                throw new ApiError(0, r?.detail || AUDIT_FAILED);
            setData(r);
            setStatus(totalsLine(r));
        }
        catch (e) {
            if (alive.current) {
                setData(null);
                setStatus(reason(e, AUDIT_FAILED));
            }
        }
        finally {
            if (alive.current)
                setLoading(false);
        }
    }, []);
    useEffect(() => { void scan(); }, [scan]);
    const changed = () => { void qc.invalidateQueries({ queryKey: ['entities'] }); void qc.invalidateQueries({ queryKey: ['performer'] }); };
    const merge = (group, keepId, ids) => {
        const c = manualConfirm(group, keepId, ids);
        if (!c.ok)
            return void toast(c.message);
        const keepName = (group.performers ?? []).find((p) => p.id === keepId)?.name ?? '';
        setAsk({ title: c.title, text: c.text, ok: c.okLabel, run: async () => {
                setBusy(true);
                setStatus(`Merging ${ids.length} selected performer record(s)…`);
                try {
                    const r = await api('/api/performers/merge-batch', { method: 'POST', json: mergeBody(keepId, ids) });
                    if (!r?.ok)
                        throw new ApiError(0, r?.detail || MERGE_FAILED);
                    changed();
                    await scan();
                    toast(mergedMessage(r, ids.length, keepName));
                }
                catch (e) {
                    setStatus(reason(e, MERGE_FAILED));
                }
                finally {
                    setBusy(false);
                }
            } });
    };
    const auto = () => {
        if (!data)
            return;
        const c = autoConfirm(data);
        if (!c.eligible)
            return void toast(NOTHING_SAFE);
        setAsk({ title: c.title, text: c.text, ok: c.okLabel, run: async () => {
                setBusy(true);
                setStatus(`Safely merging ${c.rows} duplicate performer record(s)…`);
                try {
                    const r = await api('/api/performers/duplicate-audit/auto-cleanup', { method: 'POST', json: {} });
                    if (r?.ok !== true)
                        throw new ApiError(0, r?.detail || AUTO_FAILED);
                    changed();
                    const o = autoOutcome(r);
                    setOutcome(o);
                    if (r.audit) {
                        setData(r.audit);
                        setStatus(`${o} ${totalsLine(r.audit)}`);
                    }
                    else
                        await scan();
                    toast(autoToast(r));
                }
                catch (e) {
                    const m = reason(e, AUTO_FAILED);
                    setOutcome(m);
                    toast(m);
                }
                finally {
                    setBusy(false);
                }
            } });
    };
    const junk = (group, action) => {
        const bad = junkCheck(group, action);
        if (bad)
            return void toast(bad);
        const p = group.performers[0];
        const c = junkConfirm(group, action);
        const targetName = group.suggested_target?.name || 'the selected target';
        setAsk({ title: c.title, text: c.text, ok: c.okLabel, run: async () => {
                setBusy(true);
                try {
                    const r = await api('/api/performers/junk-cleanup', { method: 'POST', json: junkBody(p.id, action, action === 'merge' ? group.suggested_target.id : null) });
                    if (!r?.ok)
                        throw new ApiError(0, r?.detail || JUNK_FAILED);
                    const o = junkOutcome(action, p.name ?? '', r, targetName);
                    setOutcome(o);
                    changed();
                    await scan();
                    toast(o);
                }
                catch (e) {
                    toast(reason(e, JUNK_FAILED));
                }
                finally {
                    setBusy(false);
                }
            } });
    };
    const groups = data?.groups ?? [];
    const eligible = data ? autoGroups(data).length : 0;
    let previous = '';
    return (_jsxs(Dialog, { wide: true, open: true, onClose: () => !busy && onClose(), title: "Duplicate performers", footer: _jsxs("div", { className: "flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3", children: [_jsx("button", { type: "button", disabled: busy || loading, onClick: () => void scan(), className: BTN, children: "Run audit again" }), _jsx("button", { type: "button", disabled: busy || loading || !eligible, onClick: auto, className: PRIMARY, children: autoLabel(eligible) })] }), children: [_jsxs("div", { className: "grid gap-3 text-left", children: [_jsx("p", { role: "status", "aria-live": "polite", className: "text-xs text-muted", children: outcome && data && !loading && !status.startsWith(outcome) ? `${outcome} ${status}` : status }), loading ? _jsx("div", { className: "rounded-2xl border border-line p-3.5 text-sm text-muted", children: AUDIT_NOTE })
                        : !data ? null
                            : !groups.length ? _jsxs("div", { className: "rounded-2xl border border-line p-3.5", children: [_jsx("b", { children: NO_GROUPS_TITLE }), _jsx("p", { className: "mt-1 text-xs text-muted", children: NO_GROUPS_NOTE })] })
                                : groups.map((g, i) => {
                                    const head = g.kind !== previous ? (previous = g.kind, _jsx("h4", { className: "mt-2 text-xs font-bold uppercase tracking-wide text-muted", children: headingFor(g.kind) }, `h${i}`)) : null;
                                    return _jsxs("div", { className: cn('grid gap-3'), children: [head, g.kind === 'junk_fragment' ? _jsx(Fragment, { group: g, busy: busy, onAct: junk }) : _jsx(GroupCard, { group: g, busy: busy, onMerge: merge })] }, i);
                                }), data && truncatedNote(data) ? _jsx("p", { className: "text-[11px] text-muted", children: truncatedNote(data) }) : null, data && groups.length ? _jsx("p", { className: "text-[11px] text-muted", children: "Records without reliable identity evidence are not paired and should be kept separate." }) : null] }), ask ? _jsx(Confirm, { title: ask.title, text: ask.text, ok: ask.ok, danger: true, onCancel: () => setAsk(null), onOk: () => { const run = ask.run; setAsk(null); void run(); } }) : null] }));
}
