import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** Eyebrow, title and subtitle: the heading block every classic page starts with. */
export function PageHead({ eyebrow = 'Library', title, blurb, children }) {
    return (_jsxs("header", { className: "mb-5 flex flex-wrap items-end justify-between gap-4", children: [_jsxs("div", { children: [_jsx("p", { className: "mb-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-accent", children: eyebrow }), _jsx("h1", { className: "text-[clamp(25px,2vw,32px)] font-extrabold leading-[1.12] tracking-[-0.03em]", children: title }), blurb ? _jsx("p", { className: "mt-1.5 text-[15px] text-muted", children: blurb }) : null] }), children] }));
}
