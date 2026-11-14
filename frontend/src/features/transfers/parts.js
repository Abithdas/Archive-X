import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useRef } from 'react';
import { ApiError } from '@/api/client';
import { cn } from '@/design/cn';
export const CARD = 'flex flex-col rounded-[18px] border border-line bg-surface p-4 sm:p-5';
export const FIELD = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg outline-none placeholder:text-faint focus:border-accent/60';
export const detail = (e, fallback) => (e instanceof ApiError ? e.detail || fallback : fallback);
export function Check({ checked, onChange, title, hint, disabled, icon }) {
    return (_jsxs("label", { className: cn('flex cursor-pointer items-start gap-3 rounded-xl p-2 hover:bg-fg/5', !!icon && 'items-center', !!icon && checked && 'bg-good/10', disabled && 'cursor-not-allowed opacity-50'), children: [_jsx("input", { type: "checkbox", checked: checked, disabled: disabled, onChange: (e) => onChange(e.target.checked), className: icon ? 'peer sr-only' : 'mt-1 size-4 shrink-0 accent-[var(--color-accent)]' }), icon ? (
            // Channels: no checkbox; a small green tick on the picture shows what is selected.
            _jsxs("span", { className: "relative grid size-11 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-bold peer-focus-visible:ring-2 peer-focus-visible:ring-accent", children: [icon, checked ? _jsx("span", { "aria-hidden": "true", className: "absolute -right-1 -top-1 grid size-[18px] place-items-center rounded-full bg-good text-white ring-2 ring-surface", children: _jsx("svg", { viewBox: "0 0 24 24", className: "size-3", fill: "none", stroke: "currentColor", strokeWidth: "3.4", strokeLinecap: "round", strokeLinejoin: "round", children: _jsx("path", { d: "M5 12.5l4.5 4.5L19 7.5" }) }) }) : null] })) : null, _jsxs("span", { className: "min-w-0", children: [_jsx("b", { className: "block text-sm", children: title }), hint ? _jsx("small", { className: "text-xs text-muted", children: hint }) : null] })] }));
}
export function Field({ label, hint, children }) {
    return (_jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: [label, children, hint ? _jsx("span", { className: "text-[11px] font-normal text-faint", children: hint }) : null] }));
}
/** Numbered step pills with a progress bar; the active step scrolls into view on a phone. */
export function Stepper({ steps, step, onStep, canGo }) {
    const list = useRef(null);
    useEffect(() => {
        list.current?.querySelector('[aria-current=step]')?.scrollIntoView?.({ inline: 'center', block: 'nearest' });
    }, [step]);
    const pct = Math.round(((step + 1) / steps.length) * 100);
    return (_jsxs(_Fragment, { children: [_jsx("ol", { ref: list, className: "my-4 flex max-w-full gap-2 overflow-x-auto [scrollbar-width:none]", "aria-label": "Steps", children: steps.map((s, i) => (_jsx("li", { children: _jsxs("button", { type: "button", "aria-current": i === step ? 'step' : undefined, onClick: () => (i <= step || canGo) && onStep(i), className: cn('flex h-10 items-center gap-2 whitespace-nowrap rounded-full px-3 text-sm font-semibold', i === step ? 'bg-surface-3 text-fg' : 'text-muted'), children: [_jsx("span", { className: cn('grid size-6 place-items-center rounded-full text-xs font-bold', i === step ? 'bg-accent text-accent-fg' : i < step ? 'bg-good/20 text-good' : 'bg-surface-3'), children: i + 1 }), s] }) }, s))) }), _jsx("div", { className: "mb-4 h-1 overflow-hidden rounded-full bg-surface-3", role: "progressbar", "aria-label": "Workflow progress", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct, children: _jsx("span", { className: "block h-full bg-accent transition-[width]", style: { width: `${pct}%` } }) })] }));
}
/** "12 videos" for a channel row; a channel that has not been scanned yet counts as 0 rather than showing "undefined". */
export const videosHint = (n) => { const v = Number(n); const c = Number.isFinite(v) && v > 0 ? v : 0; return `${c.toLocaleString()} video${c === 1 ? '' : 's'}`; };
