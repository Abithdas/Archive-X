import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { useSuggestions } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { cn } from '@/design/cn';
import { NO_FILTERS, useLibraryStore } from './store';
const FIELD = 'h-11 w-full rounded-xl border border-line-2 bg-bg px-3 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60';
const CHIP = 'h-8 rounded-full border px-3 text-[13px] font-semibold transition-colors';
export const RESOLUTIONS = [
    { value: '', label: 'Any' },
    { value: '2160', label: '4K' },
    { value: '1440', label: '1440p' },
    { value: '1080', label: '1080p' },
    { value: '720', label: '720p' },
    { value: '480', label: '<720p' },
];
export const QUICK_RANGES = [
    { days: 7, label: '7 days' },
    { days: 30, label: '30 days' },
    { days: 365, label: '1 year' },
];
/** Local calendar date as YYYY-MM-DD (toISOString would shift it to UTC and can land on the wrong day). */
export const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export function rangeEndingToday(days, now = new Date()) {
    const from = new Date(now);
    from.setDate(from.getDate() - days);
    return { dateFrom: isoDay(from), dateTo: isoDay(now) };
}
function Label({ children }) {
    return _jsx("span", { className: "text-xs font-semibold text-muted", children: children });
}
/** The classic "Filters" sheet: studio, performer, tag, upload date (with quick ranges) and resolution. Body scrolls, Apply stays put. */
export function FiltersDrawer({ open, onClose }) {
    const applied = useLibraryStore((s) => s.filters);
    const setFilters = useLibraryStore((s) => s.setFilters);
    const [draft, setDraft] = useState(applied);
    const sugg = useSuggestions(open);
    useEffect(() => {
        if (open)
            setDraft(applied);
    }, [open, applied]);
    const set = (k) => (e) => setDraft({ ...draft, [k]: e.target.value });
    const apply = () => {
        setFilters({ ...draft, studio: draft.studio.trim(), performer: draft.performer.trim(), tag: draft.tag.trim() });
        onClose();
    };
    const text = (k, label, icon, ph, list) => (_jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { icon: icon, children: label }), _jsx("input", { className: FIELD, list: `sg-${k}`, value: draft[k], onChange: set(k), placeholder: ph, autoComplete: "off" }), _jsx("datalist", { id: `sg-${k}`, children: (list ?? []).map((n) => _jsx("option", { value: n }, n)) })] }));
    return (_jsx(Dialog, { open: open, onClose: onClose, title: "Filters", side: "right", actions: _jsx("button", { type: "button", onClick: () => setDraft(NO_FILTERS), className: "h-9 rounded-xl border border-line-2 px-3 text-[13px] font-semibold", children: "Reset" }), footer: _jsx("button", { type: "submit", form: "filters-form", className: "h-12 w-full rounded-xl bg-accent text-[15px] font-bold text-accent-fg hover:brightness-105", children: "Apply filters" }), children: _jsxs("form", { id: "filters-form", className: "grid gap-4", onSubmit: (e) => { e.preventDefault(); apply(); }, children: [text('studio', 'Studio', 'studio', 'Any studio…', sugg.data?.studios), text('performer', 'Performer', 'user', 'Any performer…', sugg.data?.performers), text('tag', 'Tag', 'tag', 'Any tag…', sugg.data?.tags), _jsxs("div", { className: "grid gap-1.5", children: [_jsx(Label, { icon: "clock", children: "Upload date" }), _jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsx("input", { type: "date", "aria-label": "Upload date from", className: FIELD, value: draft.dateFrom, onChange: set('dateFrom'), max: draft.dateTo || undefined }), _jsx("input", { type: "date", "aria-label": "Upload date to", className: FIELD, value: draft.dateTo, onChange: set('dateTo'), min: draft.dateFrom || undefined })] }), _jsx("div", { role: "group", "aria-label": "Quick upload date ranges", className: "flex flex-wrap gap-1.5", children: QUICK_RANGES.map((r) => (_jsx("button", { type: "button", onClick: () => setDraft({ ...draft, ...rangeEndingToday(r.days) }), className: cn(CHIP, 'border-line bg-surface-2 text-muted hover:text-fg'), children: r.label }, r.days))) })] }), _jsxs("div", { className: "grid gap-1.5", children: [_jsx(Label, { icon: "picture", children: "Resolution" }), _jsx("div", { role: "group", "aria-label": "Resolution", className: "flex flex-wrap gap-1.5", children: RESOLUTIONS.map((r) => (_jsx("button", { type: "button", "aria-pressed": draft.resolution === r.value, onClick: () => setDraft({ ...draft, resolution: r.value }), className: cn(CHIP, draft.resolution === r.value ? 'border-accent/60 bg-accent/15 text-accent' : 'border-line bg-surface-2 text-muted hover:text-fg'), children: r.label }, r.value))) })] })] }) }));
}
