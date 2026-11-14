import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMemo } from 'react';
import { useStatus } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { buildNotifications, groupByDay } from './notifications';
import { useUi } from './ui';
const TONE = { error: 'bg-bad/15 text-bad', warning: 'bg-warn/15 text-warn', success: 'bg-good/15 text-good', match: 'bg-accent/15 text-accent', info: 'bg-surface-3 text-muted' };
/** Notices derived from the live status stream. The same list feeds the header badge. */
export function useNotices() {
    const { data } = useStatus();
    const events = data?.events;
    const jobs = data?.jobs;
    return useMemo(() => buildNotifications(Array.isArray(events) ? events : [], Array.isArray(jobs) ? jobs : []), [events, jobs]);
}
export function NotificationsPanel() {
    const open = useUi((s) => s.notifications);
    const set = useUi((s) => s.setNotifications);
    const rows = useNotices();
    const groups = groupByDay(rows);
    return (_jsxs(Dialog, { open: open, onClose: () => set(false), title: "Notifications", side: "right", children: [_jsx("p", { className: "mb-3 text-sm text-muted", children: "Important updates from your library" }), rows.length ? (groups.map(([label, items]) => (_jsxs("section", { "aria-label": label, className: "mb-3", children: [_jsx("h3", { className: "mb-1.5 text-xs font-bold text-faint", children: label }), _jsx("ul", { className: "grid gap-1.5", children: items.map((r) => (_jsxs("li", { className: "flex items-start gap-3 rounded-xl bg-bg p-3", children: [_jsx("span", { className: cn('grid size-8 shrink-0 place-items-center rounded-full', TONE[r.tone]), children: _jsx(Icon, { name: r.icon, className: "size-4" }) }), _jsxs("div", { className: "min-w-0 text-sm", children: [_jsx("p", { className: "font-semibold [overflow-wrap:anywhere]", children: r.title }), r.meta ? _jsx("p", { className: "text-xs text-muted", children: r.meta }) : null] })] }, r.key))) })] }, label)))) : (_jsxs("div", { className: "py-6", children: [_jsx("p", { className: "font-bold", children: "You\u2019re all caught up" }), _jsx("p", { className: "text-sm text-muted", children: "Important library and system updates will appear here." })] }))] }));
}
