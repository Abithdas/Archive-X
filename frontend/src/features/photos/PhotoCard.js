import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { dropdownPosition } from '@/design/placement';
import { useId, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Icon } from '@/design/Icon';
import { Skeleton } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { photoName, ratio } from './photos';
const OPTION = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none';
/** One photo tile at its own aspect ratio, with a ⋯ menu (open, copy filename, remove, delete from Telegram too). */
export function PhotoCard({ photo, favorite, onOpen, onRemove, onDifferent }) {
    const id = useId();
    // The tile shimmers until its picture has arrived, then the picture fades in (a cached picture shows at once).
    const [ready, setReady] = useState(false);
    const [failed, setFailed] = useState(false);
    const pop = useRef(null);
    const btn = useRef(null);
    const name = photoName(photo);
    const place = () => {
        const el = pop.current, b = btn.current?.getBoundingClientRect();
        if (!el || !b)
            return;
        const { left, top, maxHeight } = dropdownPosition(b, btn.current, 260, el.offsetHeight || 220, 'right');
        el.style.left = `${left}px`;
        el.style.top = `${top}px`;
        el.style.maxHeight = `${maxHeight}px`;
        el.style.overflowY = 'auto';
    };
    const close = () => pop.current?.hidePopover?.();
    const copy = async () => {
        close();
        try {
            await navigator.clipboard.writeText(name);
            toast('Filename copied');
        }
        catch {
            toast.error('Could not copy the filename.');
        }
    };
    return (_jsxs("article", { title: name, "aria-label": name, className: "group relative mb-2 block break-inside-avoid overflow-hidden rounded-xl border border-line bg-surface-2 sm:mb-3", children: [_jsxs("button", { type: "button", onClick: onOpen, "aria-label": `Open ${name}`, style: { aspectRatio: ratio(photo) }, className: "relative block w-full bg-surface-3", children: [!ready && !failed ? _jsx(Skeleton, { className: "absolute inset-0 rounded-none" }) : null, _jsx("img", { ref: (el) => { if (el?.complete && el.naturalWidth > 0)
                            setReady(true); }, src: photo.thumbnail_url, alt: "", loading: "lazy", decoding: "async", draggable: false, onLoad: () => setReady(true), onError: () => { setFailed(true); setReady(true); }, className: cn('absolute inset-0 size-full object-cover transition-[opacity,transform] duration-300 group-hover:scale-[1.03]', ready && !failed ? 'opacity-100' : 'opacity-0') })] }), favorite ? _jsx("span", { title: "Favourite", "aria-label": "Favourite", className: "pointer-events-none absolute left-2 top-2 rounded-full bg-black/55 px-1.5 text-sm text-warn", children: "\u2605" }) : null, _jsx("button", { ref: btn, type: "button", popoverTarget: id, "aria-label": `More options for ${name}`, "aria-haspopup": "menu", title: "More options", onClick: place, className: cn('absolute right-2 top-2 grid size-9 place-items-center rounded-full bg-black/55 text-white backdrop-blur-sm hover:bg-black/75 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100'), children: _jsxs("span", { className: "flex gap-[3px]", "aria-hidden": "true", children: [_jsx("i", { className: "size-1 rounded-full bg-current" }), _jsx("i", { className: "size-1 rounded-full bg-current" }), _jsx("i", { className: "size-1 rounded-full bg-current" })] }) }), _jsxs("div", { ref: pop, id: id, popover: "auto", role: "menu", "aria-label": "Photo actions", onToggle: (e) => e.nativeEvent.newState === 'open' && (place(), pop.current?.querySelector('button')?.focus()), className: "m-0 w-[260px] rounded-[16px] border border-line-2 bg-surface p-2 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]", children: [_jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => { close(); onOpen(); }, children: [_jsx(Icon, { name: "images", className: "text-muted" }), "Open photo"] }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => void copy(), children: [_jsx(Icon, { name: "duplicate", className: "text-muted" }), "Copy filename"] }), onDifferent ? _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => { close(); onDifferent(); }, children: [_jsx(Icon, { name: "user", className: "text-muted" }), "Different person \u00B7 remove from this group"] }) : null, _jsx("div", { role: "separator", className: "my-1.5 h-px bg-line" }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => { close(); onRemove(false); }, children: [_jsx(Icon, { name: "trash", className: "text-muted" }), "Remove from Archive X"] }), _jsxs("button", { type: "button", role: "menuitem", className: cn(OPTION, 'text-bad'), onClick: () => { close(); onRemove(true); }, children: [_jsx(Icon, { name: "trash" }), "Delete from Archive X and Telegram"] })] })] }));
}
