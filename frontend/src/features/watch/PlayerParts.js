import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { cn } from '@/design/cn';
const MenuCtx = createContext(() => undefined);
export const Divider = () => _jsx("span", { "aria-hidden": "true", className: "mx-0.5 hidden h-3.5 w-px shrink-0 bg-fg/15 @lg:block" });
export const Kbd = ({ children }) => _jsx("kbd", { className: "ml-auto rounded border border-line-2 px-1 text-[10px] font-semibold text-muted", children: children });
export const MenuHead = ({ children }) => _jsx("div", { className: "px-2.5 py-1 text-[10.5px] font-bold uppercase tracking-wider text-muted", children: children });
export const MenuRow = ({ k, v, good }) => (_jsxs("div", { className: "flex justify-between gap-6 px-2.5 py-1 text-xs", children: [_jsx("span", { className: "text-muted", children: k }), _jsx("span", { className: cn('font-semibold', good && 'text-emerald-400'), children: v })] }));
export function MenuItem({ children, onClick, active, pressed, label }) {
    const close = useContext(MenuCtx);
    return (_jsxs("button", { type: "button", role: pressed !== undefined ? 'menuitemcheckbox' : active !== undefined ? 'menuitemradio' : 'menuitem', "aria-checked": pressed ?? active, "aria-label": label, onClick: () => onClick(close), className: cn('flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-fg/10 hover:text-fg', active ? 'bg-fg/10 font-semibold text-fg' : 'text-muted'), children: [_jsx("span", { className: "flex min-w-0 flex-1 items-center justify-between gap-3", children: children }), active ? _jsx("span", { "aria-hidden": "true", children: "\u2713" }) : null] }));
}
/** A small menu that opens above its button (like the minimal player): closes on outside click, Escape or choosing an item. */
export function PopMenu({ label, button, children, align = 'left', iconOnly = false }) {
    const [open, setOpen] = useState(false);
    const root = useRef(null);
    useEffect(() => {
        if (!open)
            return;
        const away = (e) => { if (!root.current?.contains(e.target))
            setOpen(false); };
        const esc = (e) => { if (e.key === 'Escape') {
            e.stopPropagation();
            setOpen(false);
        } };
        document.addEventListener('pointerdown', away);
        document.addEventListener('keydown', esc, true);
        return () => { document.removeEventListener('pointerdown', away); document.removeEventListener('keydown', esc, true); };
    }, [open]);
    return (_jsxs("div", { ref: root, className: "relative", children: [_jsx("button", { type: "button", "aria-label": label, "aria-haspopup": "menu", "aria-expanded": open, title: label, onClick: () => setOpen(!open), className: cn('inline-flex items-center gap-1.5 rounded-md text-xs font-medium text-fg/85 transition-colors hover:bg-fg/10 hover:text-fg focus-visible:outline-2 focus-visible:outline-accent', iconOnly ? 'size-8 justify-center' : 'px-1.5 py-1 @lg:px-2', open && 'bg-fg/10 text-fg'), children: button }), open ? (_jsx(MenuCtx.Provider, { value: () => setOpen(false), children: _jsx("div", { role: "menu", "aria-label": label, className: cn('[[data-ov]_&]:[--color-fg:#eceff4] [[data-ov]_&]:[--color-muted:#9aa4b6] [[data-ov]_&]:[--color-surface:#2b313d] [[data-ov]_&]:[--color-line-2:#434c5e] absolute bottom-10 z-50 flex min-w-[9.5rem] flex-col gap-0.5 rounded-xl border border-line-2 bg-surface/95 p-1.5 text-fg shadow-2xl backdrop-blur-xl', align === 'right' ? 'right-0' : 'left-0'), children: children }) })) : null] }));
}
/** Thin volume slider with a round knob (shown from tablet width up). */
export function VolumeBar({ value, onChange }) {
    const bar = useRef(null);
    const drag = useRef(false);
    const at = (e) => { const r = bar.current.getBoundingClientRect(); onChange(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))); };
    const key = (e) => {
        const step = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 0.05 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -0.05 : 0;
        if (step) {
            e.preventDefault();
            e.stopPropagation();
            onChange(Math.max(0, Math.min(1, value + step)));
        }
    };
    return (_jsx("div", { ref: bar, role: "slider", tabIndex: 0, "aria-label": "Volume", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(value * 100), onKeyDown: key, onPointerDown: (e) => { drag.current = true; e.currentTarget.setPointerCapture(e.pointerId); at(e); }, onPointerMove: (e) => drag.current && at(e), onPointerUp: () => { drag.current = false; }, onPointerCancel: () => { drag.current = false; }, className: "relative hidden h-4 w-12 cursor-pointer touch-none items-center outline-none focus-visible:ring-2 focus-visible:ring-accent/60 @lg:flex @lg:w-16", children: _jsxs("div", { className: "relative h-[3px] w-full rounded-full bg-fg/20", children: [_jsx("div", { className: "pointer-events-none absolute inset-y-0 left-0 rounded-full bg-fg", style: { width: `${value * 100}%` } }), _jsx("div", { className: "pointer-events-none absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg shadow", style: { left: `${value * 100}%` } })] }) }));
}
