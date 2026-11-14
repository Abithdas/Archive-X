import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useEntities } from '@/api/hooks';
import { Button } from '@/design/Button';
import { Confirm } from '@/design/Confirm';
import { PageHead } from '@/design/PageHead';
import { Segmented } from '@/design/Segmented';
import { useSearch } from '@/app/search';
import { SkeletonGroup } from '@/design/Skeleton';
import { EntityCard, EntityCardSkeleton } from './EntityCard';
import { Pagination } from '@/features/library/Pagination';
import { filterEntities, pageCount } from './filter';
import { FAILED, IDLE, NEED_SELECTION, SORTS, canCommit, commitLabel, confirmation, duplicatesOf, hint, mergeBody, modeLabel, pick, sortBy, successMessage, toggleMode } from './merge';
import { Empty } from '@/design/Empty';
const GRID = 'grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-[18px] sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]';
const DEFAULT_PER = 12;
const COPY = {
    performers: { eyebrow: 'Library', title: 'Performers', blurb: 'Everyone linked to your videos', empty: 'No performers yet.' },
    studios: { eyebrow: 'Library', title: 'Studios', blurb: 'Studios and sites in your archive', empty: 'No studios yet.' },
};
export function EntityDirectory({ kind }) {
    const copy = COPY[kind];
    const entities = useEntities(kind);
    const query = useSearch((s) => s.q);
    const [gender, setGender] = useState('female');
    const [page, setPage] = useState(1);
    const [per, setPer] = useState(DEFAULT_PER);
    const [sort, setSort] = useState('default');
    const [merge, setMerge] = useState(IDLE);
    const [asking, setAsking] = useState(false);
    const qc = useQueryClient();
    const rows = useMemo(() => sortBy(filterEntities(entities.data ?? [], query, kind === 'performers' ? gender : 'all'), sort), [entities.data, query, gender, kind, sort]);
    const pages = pageCount(rows.length, per);
    const current = Math.min(page, pages);
    useEffect(() => setPage(1), [query, gender, kind]);
    useEffect(() => setMerge(IDLE), [kind]); // merge mode belongs to the performers list
    const visible = rows.slice((current - 1) * per, current * per);
    const all = entities.data ?? [];
    const merging = kind === 'performers' && merge.active;
    const say = (m) => m && toast(m);
    const toggle = () => { const r = toggleMode(merge); setMerge(r.state); say(r.message); };
    const pickCard = (id) => { const r = pick(merge, id, all); setMerge(r.state); say(r.message); };
    const keep = all.find((p) => p.id === merge.keepId);
    const ids = duplicatesOf(merge);
    const commit = async () => {
        if (!keep || !ids.length)
            return void toast(NEED_SELECTION);
        setAsking(false);
        setMerge((m) => ({ ...m, busy: true }));
        try {
            const r = await api('/api/performers/merge-batch', { method: 'POST', json: mergeBody(keep.id, ids) });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || FAILED);
            setMerge(IDLE);
            await qc.invalidateQueries({ queryKey: ['entities'] });
            toast(successMessage(r, ids.length, keep.name));
        }
        catch (e) {
            setMerge((m) => ({ ...m, busy: false }));
            toast.error(e instanceof ApiError && e.detail ? e.detail : FAILED);
        }
    };
    return (_jsxs("section", { className: "mx-auto flex w-full max-w-[1800px] flex-1 flex-col pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(PageHead, { title: copy.title, blurb: copy.blurb }), _jsxs("div", { className: "mb-5 flex flex-wrap items-center gap-2.5", children: [kind === 'performers' ? (_jsx(Segmented, { label: "Gender", value: gender, onChange: setGender, options: [{ value: 'female', label: 'Female' }, { value: 'male', label: 'Male' }, { value: 'trans', label: 'Trans' }, { value: 'all', label: 'All' }] })) : null, kind === 'performers' ? (_jsxs(_Fragment, { children: [_jsxs("label", { className: "inline-flex items-center gap-2 text-sm text-muted", children: ["Sort", _jsx("select", { value: sort, onChange: (e) => setSort(e.target.value), "aria-label": "Sort performers", className: "h-10 rounded-xl border border-line-2 bg-surface px-3 text-sm font-semibold text-fg", children: SORTS.map((o) => _jsx("option", { value: o.value, children: o.label }, o.value)) })] }), _jsx(Button, { variant: merge.active ? 'primary' : 'outline', disabled: merge.busy, onClick: toggle, children: modeLabel(merge.active) }), merge.active ? _jsx(Button, { variant: "primary", disabled: !canCommit(merge, all), onClick: () => setAsking(true), children: commitLabel(merge.mergeIds.length) }) : null, merge.active ? _jsx("span", { role: "status", "aria-live": "polite", className: "max-w-[480px] text-xs text-muted", children: hint(merge, all) }) : null] })) : null, entities.data ? _jsxs("span", { className: "ml-auto text-sm text-muted", children: [rows.length, " ", copy.title.toLowerCase()] }) : null] }), entities.isPending ? (_jsx(SkeletonGroup, { label: `Loading ${copy.title.toLowerCase()}`, className: GRID, children: Array.from({ length: 12 }, (_, i) => _jsx(EntityCardSkeleton, { kind: kind }, i)) })) : entities.isError ? (_jsxs("div", { role: "alert", className: "grid place-items-center gap-3 rounded-card border border-line py-16 text-center", children: [_jsxs("p", { className: "font-semibold", children: ["Could not load ", copy.title.toLowerCase(), "."] }), _jsx(Button, { variant: "primary", onClick: () => void entities.refetch(), children: "Retry" })] })) : !rows.length ? (_jsxs(Empty, { icon: kind === 'performers' ? 'user' : 'studio', children: [entities.data.length ? (kind === 'performers' && gender !== 'all' && !query.trim() ? `No ${gender} performers. ` : 'No matches. ') : copy.empty, entities.data.length && kind === 'performers' && gender !== 'all' ? _jsx("button", { type: "button", onClick: () => setGender('all'), className: "font-semibold text-accent underline", children: "Show all performers" }) : null] })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: GRID, children: visible.map((e, i) => _jsx(EntityCard, { index: i, kind: kind, entity: e, merge: merging ? { keeper: merge.keepId === e.id, selected: merge.mergeIds.includes(e.id), onPick: () => pickCard(e.id) } : undefined }, e.id)) }), _jsx(Pagination, { page: current - 1, pages: pages, per: per, onPage: (n) => setPage(n + 1), onPer: (n) => { setPer(n); setPage(1); } })] })), asking && keep ? (() => { const c = confirmation(keep, ids, all); return _jsx(Confirm, { title: c.title, text: c.text, ok: c.ok, danger: true, onCancel: () => setAsking(false), onOk: () => void commit() }); })() : null] }));
}
