import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { cn } from '@/design/cn';
import { BlurImg } from '@/design/BlurImg';
import { bestGuess, groupLabel, isAssigned, peopleRow, viewFilter } from './faces';
import { usePhotoStore } from './store';
function Face({ g, active, onPick }) {
    const [ok, setOk] = useState(true);
    const named = isAssigned(g);
    const guess = bestGuess(g);
    return (_jsxs("button", { type: "button", onClick: onPick, "aria-pressed": active, title: named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Who is this?', className: "group grid w-[76px] shrink-0 content-start justify-items-center gap-1 text-center text-xs", children: [_jsx("span", { className: cn('relative grid size-16 place-items-center overflow-hidden rounded-full bg-surface-2 ring-2 ring-offset-2 ring-offset-bg transition', active ? 'ring-accent' : named ? 'ring-transparent group-hover:ring-accent/50' : 'ring-warn/50 group-hover:ring-warn'), children: ok && g.representative_url ? _jsx(BlurImg, { src: g.representative_url, alt: "", loading: "lazy", decoding: "async", onError: () => setOk(false), className: "size-full object-cover" }) : _jsx("span", { "aria-hidden": "true", className: "text-lg font-bold text-muted", children: "?" }) }), _jsx("span", { className: cn('line-clamp-2 w-full leading-[14px]', named ? 'font-semibold text-fg' : 'text-muted'), children: named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Add name' }), _jsxs("span", { className: "text-[10.5px] text-faint", children: [Number(g.photo_count ?? 0).toLocaleString(), " photos"] })] }));
}
/** People across the top of the photo library. Click a person to see their photos; a person without a name opens Image Analysis to confirm who it is. */
export function PeopleRow({ groups, onManage }) {
    const { face, setFace } = usePhotoStore();
    const row = peopleRow(groups);
    if (!row.length)
        return null;
    return (_jsxs("section", { "aria-label": "People", className: "mb-4", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between", children: [_jsx("h2", { className: "text-lg font-bold", children: "People" }), _jsx("button", { type: "button", onClick: onManage, className: "text-xs font-semibold text-accent hover:underline", children: "Manage people" })] }), _jsx("div", { className: "flex gap-3 overflow-x-auto pb-2 pt-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden", children: row.map((g) => {
                    const active = face?.groupId === Number(g.id);
                    return _jsx(Face, { g: g, active: active, onPick: () => (active ? setFace(null) : isAssigned(g) || Number(g.photo_count ?? 0) > 0 ? setFace(viewFilter(g).filter) : onManage()) }, g.id);
                }) })] }));
}
