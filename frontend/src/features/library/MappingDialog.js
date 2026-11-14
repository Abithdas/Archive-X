import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { NO_CHANGES, NO_STUDIOS_HINT, SAVED, choiceLabel, choiceNote, failureMessage, mappingBody, mappingTasks, pickedFrom, studioChoices } from './mapping';
import { useDebounced } from './useDebounced';
const INPUT = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60';
/** "Mapping / report": correct one video's title and studio (both are locked so provider syncs do not undo them). */
export function MappingDialog({ item, onDone }) {
    const qc = useQueryClient();
    const listId = useId();
    const originalStudio = item.studio || item.studios[0] || '';
    const [title, setTitle] = useState(item.name);
    const [studio, setStudio] = useState(originalStudio ? { name: originalStudio, id: null } : null);
    const [q, setQ] = useState('');
    const [active, setActive] = useState(0);
    const [busy, setBusy] = useState(false);
    const term = useDebounced(q.trim(), 120);
    const search = useQuery({ queryKey: ['studio-search', term], queryFn: () => api(`/api/studios?limit=20&q=${encodeURIComponent(term)}`), enabled: term !== '', staleTime: 10_000, retry: false });
    const choices = q.trim() ? studioChoices(Array.isArray(search.data) ? search.data : [], q) : [];
    const shown = term === q.trim() ? choices : []; // do not show stale hits for what was typed a moment ago
    const pick = (i) => { const c = shown[i]; if (c) {
        setStudio(pickedFrom(c));
        setQ('');
        setActive(0);
    } };
    const onKey = (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActive((a) => Math.min(shown.length - 1, a + 1));
        }
        else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive((a) => Math.max(0, a - 1));
        }
        else if (e.key === 'Enter' && shown[active]) {
            e.preventDefault();
            pick(active);
        }
    };
    const save = async () => {
        const tasks = mappingTasks({ title, originalTitle: item.name, studio, originalStudio });
        if (!tasks.length)
            return void toast(NO_CHANGES);
        setBusy(true);
        const applied = [];
        try {
            for (const task of tasks) {
                const r = await api('/api/mapping/bulk', { method: 'POST', json: mappingBody(item, task) });
                if (!r?.ok)
                    throw new ApiError(0, r?.detail || '');
                applied.push(task.field === 'site' ? 'studio' : 'title');
            }
            toast(SAVED);
            void qc.invalidateQueries({ queryKey: ['library'] });
            void qc.invalidateQueries({ queryKey: ['entities'] });
            void qc.invalidateQueries({ queryKey: ['video'] });
            onDone();
        }
        catch (e) {
            toast.error(failureMessage(e instanceof ApiError ? e.detail : '', applied));
            if (applied.length) {
                void qc.invalidateQueries({ queryKey: ['library'] });
            }
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsx(Dialog, { open: true, onClose: () => !busy && onDone(), title: "Correct video metadata", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Video name / title", _jsx("input", { value: title, onChange: (e) => setTitle(e.target.value), placeholder: "Correct video title", className: INPUT, autoFocus: true })] }), _jsxs("div", { className: "grid gap-1.5", children: [_jsx("span", { className: "text-xs font-semibold text-muted", id: `${listId}-lbl`, children: "Studio" }), _jsx("div", { className: "min-h-9", children: studio ? (_jsxs("span", { className: "inline-flex max-w-full items-center gap-1.5 rounded-full border border-line-2 bg-bg py-1 pl-3 pr-1.5 text-sm font-semibold", children: [_jsx("span", { className: "truncate", children: studio.name }), _jsx("button", { type: "button", "aria-label": "Remove studio", title: "Remove studio", onClick: () => setStudio(null), className: "grid size-6 place-items-center rounded-full text-muted hover:bg-fg/10 hover:text-fg", children: _jsx(Icon, { name: "close", className: "size-3.5" }) })] })) : _jsx("span", { className: "text-sm text-muted", children: "No studio selected." }) }), _jsx("input", { role: "combobox", "aria-expanded": shown.length > 0, "aria-controls": listId, "aria-labelledby": `${listId}-lbl`, "aria-activedescendant": shown.length ? `${listId}-${active}` : undefined, "aria-autocomplete": "list", value: q, onChange: (e) => { setQ(e.target.value); setActive(0); }, onKeyDown: onKey, placeholder: "Search studio or enter a new studio name", autoComplete: "off", className: INPUT }), _jsx("div", { id: listId, role: "listbox", "aria-label": "Studios", className: "max-h-60 overflow-y-auto rounded-xl border border-line", children: !q.trim() ? _jsx("p", { className: "p-3 text-sm text-muted", children: NO_STUDIOS_HINT })
                                : term !== q.trim() || search.isFetching ? _jsx("p", { className: "p-3 text-sm text-muted", role: "status", children: "Searching\u2026" })
                                    : shown.length ? shown.map((c, i) => (_jsxs("div", { id: `${listId}-${i}`, role: "option", "aria-selected": i === active, onMouseDown: (e) => { e.preventDefault(); pick(i); }, className: cn('flex cursor-pointer items-center gap-3 px-3 py-2.5 hover:bg-fg/5', i === active && 'bg-accent/15'), children: [_jsx("span", { className: "grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-muted", "aria-hidden": "true", children: _jsx(Icon, { name: c.create ? 'plus' : 'circle', className: "size-3.5" }) }), _jsxs("span", { className: "min-w-0", children: [_jsx("span", { className: "block truncate text-sm", children: choiceLabel(c) }), choiceNote(c) ? _jsx("span", { className: "block text-[11px] text-muted", children: choiceNote(c) }) : null] })] }, `${c.id ?? 'new'}-${c.name}`))) : _jsx("p", { className: "p-3 text-sm text-muted", children: "No matching studios." }) })] }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", disabled: busy, onClick: onDone, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", disabled: busy, onClick: () => void save(), className: "h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60", children: busy ? 'Saving…' : 'Save changes' })] })] }) }));
}
