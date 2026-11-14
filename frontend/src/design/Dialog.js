import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useId, useRef } from 'react';
import { Icon } from './Icon';
import { cn } from './cn';
/** Modal on the native <dialog> (focus trap, Esc, top layer), blurred backdrop, as a right-hand drawer or a centred card. */
export function Dialog({ open, onClose, title, children, side = 'center', labelledBy, actions, footer, wide = false }) {
    const ref = useRef(null);
    const autoId = useId();
    const titleId = labelledBy ?? autoId;
    useEffect(() => {
        const d = ref.current;
        if (!d)
            return;
        if (open && !d.open)
            d.showModal();
        if (!open && d.open)
            d.close();
    }, [open]);
    return (_jsxs("dialog", { ref: ref, "aria-labelledby": titleId, onClose: onClose, onClick: (e) => e.target === ref.current && onClose(), className: cn('fixed m-0 flex-col overflow-hidden rounded-[24px] border border-line-2 bg-surface p-4 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)] backdrop:bg-black/55 backdrop:backdrop-blur-sm open:flex', wide ? 'w-[min(1040px,calc((100vw-24px)/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1))))]' : 'w-[min(400px,calc((100vw-24px)/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1))))]', side === 'right'
            ? 'left-auto right-3 top-3 max-h-[calc((100dvh-24px)/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1)))] sm:right-4 sm:top-[72px] sm:max-h-[calc((100dvh-84px*var(--ax-ui-zoom,1)*var(--ax-page-zoom,1))/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1)))]'
            : 'inset-0 m-auto max-h-[calc((100dvh-24px)/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1)))]'), children: [_jsx("span", { "aria-hidden": "true", className: "mx-auto mb-3 block h-1 w-9 shrink-0 rounded-full bg-fg/20 sm:hidden" }), _jsxs("div", { className: "mb-3 flex shrink-0 items-center justify-between gap-2", children: [_jsx("h2", { id: titleId, className: "text-lg font-extrabold", children: title }), _jsx("div", { className: "ml-auto flex items-center gap-2", children: actions }), _jsx("button", { type: "button", onClick: onClose, "aria-label": "Close", className: "grid size-9 place-items-center rounded-xl border border-line-2 text-muted hover:text-fg", children: _jsx(Icon, { name: "close", className: "size-4" }) })] }), _jsx("div", { className: "min-h-0 flex-1 overflow-y-auto overscroll-contain", children: children }), footer ? _jsx("div", { className: "shrink-0 pt-3", children: footer }) : null] }));
}
