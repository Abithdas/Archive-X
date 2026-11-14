import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Link, useRouter } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { SelectionBar } from '@/features/library/SelectionBar';
import { useSelection } from '@/features/library/selection';
/** The shared look of the performer and studio profile pages (values measured from the classic pages). */
/** Back button plus "Performers › Name". */
export function BackBar({ to, section, name }) {
    const router = useRouter();
    const back = () => { if (window.history.length > 1)
        router.history.back();
    else
        void router.navigate({ to }); };
    return (_jsxs("nav", { "aria-label": "Breadcrumb", className: "mb-[18px] flex min-h-10 items-center gap-3 text-[13px] font-medium text-muted", children: [_jsx("button", { type: "button", onClick: back, "aria-label": "Go back", title: "Go back", className: "grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-surface text-fg hover:bg-surface-2", children: _jsx(Icon, { name: "arrow-left", className: "size-[18px]" }) }), _jsxs("span", { className: "min-w-0 [overflow-wrap:anywhere]", children: [_jsx(Link, { to: to, className: "hover:text-fg hover:underline", children: section }), " ", _jsx("span", { "aria-hidden": "true", children: "\u203A" }), " ", name] })] }));
}
/** Card with the picture on the left (above the text on a phone). */
export function HeroCard({ label, media, children }) {
    return (_jsxs("section", { "aria-label": label, className: "mb-5 grid grid-cols-1 items-start gap-[13px] rounded-2xl border border-line bg-surface px-4 py-5 sm:grid-cols-[200px_minmax(0,1fr)] sm:gap-[22px] sm:rounded-[18px] sm:p-[18px]", children: [_jsx("div", { className: "mx-auto w-[150px] sm:mx-0 sm:w-[200px]", children: media }), _jsx("div", { className: "min-w-0", children: children })] }));
}
export const MEDIA_FRAME = 'relative aspect-[3/4] w-full overflow-hidden rounded-[20px] border border-line-2 bg-surface-2 shadow-[0_18px_36px_-12px_rgb(0_0_0/0.55)]';
export const Eyebrow = ({ children }) => _jsx("p", { className: "mb-[3px] text-[10.5px] font-bold uppercase tracking-[0.12em] text-accent", children: children });
export const Title = ({ children }) => _jsx("h1", { className: "mb-[9px] text-[28px] font-extrabold leading-[1.12] tracking-[-0.035em] [overflow-wrap:anywhere] sm:text-[34px]", children: children });
export const Pills = ({ children }) => _jsx("div", { className: "mb-2.5 flex flex-wrap items-center gap-1.5", children: children });
const TONES = {
    plain: 'border-line bg-surface-2 text-muted',
    local: 'border-line bg-surface-2 text-muted',
    linked: 'border-line bg-[rgb(52_211_153/0.14)] text-good',
    violet: 'border-[rgb(140_200_255/0.38)] bg-[rgb(140_200_255/0.15)] text-[rgb(173_214_251)]',
    accent: 'border-accent/45 bg-accent/[0.13] text-accent',
};
export function Pill({ tone = 'plain', children }) {
    return (_jsxs("span", { className: cn('inline-flex h-[26px] items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 text-xs font-semibold', TONES[tone]), children: [tone === 'linked' || tone === 'local' ? _jsx("i", { "aria-hidden": "true", className: cn('size-[7px] rounded-full', tone === 'linked' ? 'bg-[#34d399]' : 'bg-faint') }) : null, children] }));
}
/** Provider chip colours: StashDB and ThePornDB each have their own. */
export const providerTone = (t) => (t === 'violet' ? 'violet' : t === 'accent' ? 'accent' : 'plain');
export function Bio({ text, limit = 280 }) {
    const [open, setOpen] = useState(false);
    if (!text)
        return null;
    const long = text.length > limit;
    return (_jsxs("p", { className: "mt-2 max-w-[750px] border-b border-fg/[0.07] pb-[15px] text-[13px] leading-[1.65] text-muted", children: [long && !open ? `${text.slice(0, limit).trimEnd()}…` : text, ' ', long ? _jsx("button", { type: "button", className: "font-semibold text-accent", onClick: () => setOpen(!open), "aria-expanded": open, children: open ? 'Show less' : 'Read more' }) : null] }));
}
export const ACTION = 'inline-flex h-9 items-center gap-[7px] rounded-[10px] border border-line bg-surface px-3 text-xs font-semibold text-muted hover:bg-fg/5 hover:text-fg disabled:opacity-60';
export const ACTION_PRIMARY = 'inline-flex h-9 items-center gap-[7px] rounded-[10px] border border-transparent bg-accent px-3 text-xs font-semibold text-accent-fg hover:brightness-110 disabled:opacity-60';
export const Actions = ({ children }) => _jsx("div", { className: "mb-2 mt-3 flex flex-wrap items-center gap-[7px]", children: children });
/** Page tabs: quiet labels, an accent underline under the open one, and a count chip. */
export function EntityTabs({ label, tabs, value, onChange }) {
    return (_jsx("div", { role: "tablist", "aria-label": label, className: "mb-4 mt-5 flex gap-1 overflow-x-auto border-b border-line", children: tabs.map((t) => {
            const on = value === t.value;
            return (_jsxs("button", { type: "button", role: "tab", "aria-selected": on, onClick: () => onChange(t.value), className: cn('relative flex h-11 shrink-0 items-center gap-1.5 whitespace-nowrap px-3.5 text-[13.5px] font-semibold after:absolute after:inset-x-2.5 after:-bottom-px after:h-0.5 after:origin-left after:rounded-full after:bg-accent after:transition-transform', on ? 'text-fg after:scale-x-100' : 'text-muted after:scale-x-0 hover:text-fg'), children: [t.label, t.count ? _jsx("span", { className: cn('ml-0.5 grid h-[18px] place-items-center rounded-full px-[7px] text-[11px] font-semibold leading-[18px]', on ? 'bg-accent/[0.13] text-accent' : 'bg-surface-2 text-muted'), children: t.count }) : null] }, t.value));
        }) }));
}
/** "Videos linked to this …" with the button that switches on multi-select editing. Also shows the editing bar. */
export function VideosHead({ kind, videos }) {
    const active = useSelection((s) => s.active);
    const count = useSelection((s) => Object.keys(s.picked).length);
    useEffect(() => () => useSelection.getState().exit(), []);
    return (_jsx(_Fragment, { children: _jsxs("div", { className: "mb-2.5 flex items-center justify-between gap-3", children: [_jsx("div", { className: "text-[13px] text-muted", children: kind === 'performer' ? 'Videos linked to this performer' : 'Videos linked to this studio' }), videos.length ? (_jsxs("button", { type: "button", "aria-pressed": active, title: "Select several videos and change their performer, studio, title or metadata together", onClick: () => (active ? useSelection.getState().exit() : useSelection.getState().enter()), className: cn(ACTION, active && 'border-accent/50 text-accent'), children: [_jsx(Icon, { name: "edit", className: "size-4" }), _jsx("span", { children: active ? `Selecting · ${count}` : 'Edit multiple videos' })] })) : null] }) }));
}
/** The bottom bar for the picked videos; render it after the video grid. */
export function EditingBar({ videos }) {
    const active = useSelection((s) => s.active);
    return active ? _jsx(SelectionBar, { visible: videos }) : null;
}
