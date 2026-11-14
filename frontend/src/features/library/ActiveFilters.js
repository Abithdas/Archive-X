import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Icon } from '@/design/Icon';
import { RESOLUTIONS } from './FiltersDrawer';
import { useLibraryStore } from './store';
const LABELS = { studio: 'Studio', performer: 'Performer', tag: 'Tag', dateFrom: 'From', dateTo: 'To', resolution: 'Resolution' };
/** One removable chip per applied filter (the classic "active filters" strip under the summary line). */
export function ActiveFilters() {
    const filters = useLibraryStore((s) => s.filters);
    const remove = useLibraryStore((s) => s.removeFilter);
    const entries = Object.keys(LABELS).filter((k) => filters[k]);
    if (!entries.length)
        return null;
    return (_jsx("ul", { className: "mb-4 flex flex-wrap gap-2", "aria-label": "Active filters", children: entries.map((k) => (_jsxs("li", { className: "inline-flex items-center gap-1.5 rounded-full bg-accent/15 py-1 pl-3 pr-1.5 text-[13px] font-semibold text-accent", children: [LABELS[k], ": ", k === 'resolution' ? (RESOLUTIONS.find((r) => r.value === filters[k])?.label ?? filters[k]) : filters[k], _jsx("button", { type: "button", onClick: () => remove(k), "aria-label": `Remove ${LABELS[k]} filter`, className: "grid size-5 place-items-center rounded-full hover:bg-accent/20", children: _jsx(Icon, { name: "close", className: "size-3" }) })] }, k))) }));
}
