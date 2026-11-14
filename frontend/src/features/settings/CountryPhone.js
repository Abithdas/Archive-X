import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { composePhone, filterCountries, sameCountry } from './countries';
/** Phone number entry: a searchable country-code picker plus the national number. A pasted +/00 number is split on its calling code. */
export function CountryPhone({ country, local, onChange, inputClass }) {
    const [open, setOpen] = useState(false);
    const [q, setQ] = useState('');
    const [up, setUp] = useState(false);
    const wrap = useRef(null);
    const toggle = useRef(null);
    const search = useRef(null);
    const phone = useRef(null);
    const list = filterCountries(q);
    useEffect(() => {
        if (!open)
            return;
        const r = toggle.current?.getBoundingClientRect();
        if (r) {
            const below = window.innerHeight - r.bottom - 12, above = r.top - 12;
            setUp(below < Math.min(240, window.innerHeight - 40) && above > below);
        }
        search.current?.focus();
        const away = (e) => { if (!wrap.current?.contains(e.target))
            setOpen(false); };
        document.addEventListener('mousedown', away);
        return () => document.removeEventListener('mousedown', away);
    }, [open]);
    const pick = (c) => { onChange(c, local); setOpen(false); setQ(''); phone.current?.focus(); };
    const type = (v) => { const p = composePhone(v, country); onChange(p.country, /^\+|^00/.test(v.trim()) && p.local ? p.local : v); };
    return (_jsxs("div", { className: "flex gap-2", children: [_jsxs("div", { ref: wrap, className: "relative", onKeyDown: (e) => { if (e.key === 'Escape' && open) {
                    e.stopPropagation();
                    setOpen(false);
                    toggle.current?.focus();
                } }, children: [_jsxs("button", { ref: toggle, type: "button", "aria-haspopup": "listbox", "aria-expanded": open, "aria-label": `Country code ${country.name} +${country.dial}`, onClick: () => { setOpen((o) => !o); setQ(''); }, className: "inline-flex h-12 items-center gap-2 rounded-xl border border-line-2 bg-bg px-3 text-[15px] font-semibold hover:bg-fg/5", children: [_jsx("span", { children: country.iso }), _jsxs("span", { className: "text-muted", children: ["+", country.dial] }), _jsx(Icon, { name: "chevron-down", className: "size-4 text-muted" })] }), open ? (_jsxs("div", { className: cn('absolute z-30 w-[min(320px,calc(100vw-48px))] rounded-xl border border-line-2 bg-surface p-2 shadow-[0_18px_50px_rgb(0_0_0/0.5)]', up ? 'bottom-full mb-1' : 'top-full mt-1'), children: [_jsxs("label", { className: "mb-1 flex items-center gap-2 rounded-lg border border-line px-2", children: [_jsx(Icon, { name: "search", className: "size-4 text-muted" }), _jsx("input", { ref: search, type: "search", value: q, onChange: (e) => setQ(e.target.value), placeholder: "Search country\u2026", autoComplete: "off", "aria-label": "Search country", className: "h-9 min-w-0 flex-1 bg-transparent text-sm outline-none" })] }), _jsx("div", { role: "listbox", "aria-label": "Country calling codes", className: "max-h-60 overflow-y-auto", children: list.length ? list.map((c) => (_jsxs("button", { type: "button", role: "option", "aria-selected": sameCountry(c, country), onClick: () => pick(c), className: cn('flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-fg/5', sameCountry(c, country) && 'bg-accent/10'), children: [_jsx("span", { className: "w-7 font-bold", children: c.iso }), _jsx("span", { className: "min-w-0 flex-1 truncate", children: c.name }), _jsxs("span", { className: "text-muted", children: ["+", c.dial] })] }, `${c.iso}${c.dial}`))) : _jsx("p", { className: "px-2 py-2 text-sm text-muted", children: "No country matches." }) })] })) : null] }), _jsx("input", { ref: phone, type: "tel", inputMode: "tel", autoComplete: "tel-national", value: local, onChange: (e) => type(e.target.value), placeholder: "234 567 8900", "aria-label": "Phone number", className: inputClass })] }));
}
