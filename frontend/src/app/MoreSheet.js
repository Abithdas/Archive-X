import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Link } from '@tanstack/react-router';
import { useStatus } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { useUi } from './ui';
import { useNotices } from './NotificationsPanel';
const ROW = 'flex h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-semibold hover:bg-fg/5';
/** Phone "More" sheet: the destinations that do not fit in the five-slot bottom bar. */
export function MoreSheet() {
    const open = useUi((s) => s.more);
    const { setMore, setNotifications, setPalette } = useUi.getState();
    const running = useStatus().data?.jobs?.filter((j) => /^(RUNNING|ACTIVE|STARTING|RECOVERING|RETRYING)$/i.test(String(j.status))).length ?? 0;
    const notices = useNotices().length;
    const done = () => setMore(false);
    return (_jsx(Dialog, { open: open, onClose: done, title: "More", children: _jsxs("div", { className: "grid gap-0.5", children: [_jsxs(Link, { to: "/operations", onClick: done, className: ROW, children: [_jsx(Icon, { name: "operations", className: "text-muted" }), "Operations", running ? _jsxs("span", { className: "ml-auto rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent", children: [running, " running"] }) : null] }), _jsxs(Link, { to: "/settings", onClick: done, className: ROW, children: [_jsx(Icon, { name: "gear", className: "text-muted" }), "Settings"] }), _jsxs(Link, { to: "/ai", onClick: done, className: ROW, children: [_jsx(Icon, { name: "sparkle", className: "text-muted" }), "AI Assistance"] }), _jsxs("button", { type: "button", className: ROW, onClick: () => { done(); setNotifications(true); }, children: [_jsx(Icon, { name: "clock", className: "text-muted" }), "Notifications", notices ? _jsx("span", { className: "ml-auto rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent", children: notices }) : null] }), _jsxs("button", { type: "button", className: ROW, onClick: () => { done(); setPalette(true); }, children: [_jsx(Icon, { name: "search", className: "text-muted" }), "Command palette"] })] }) }));
}
