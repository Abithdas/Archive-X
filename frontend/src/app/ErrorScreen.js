import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Component } from 'react';
import { toast } from 'sonner';
import { Icon } from '@/design/Icon';
import { recentErrors } from './errorLog';
import { buildReport } from './report';
/** A page file that cannot be fetched (usually an old tab after the app was updated): retrying in place cannot work because the failed import is remembered, so reload instead. */
export const isStaleChunk = (e) => /dynamically imported module|Importing a module script failed|Loading chunk|error loading dynamically/i.test(e instanceof Error ? e.message : String(e));
const BTN = 'inline-flex h-10 items-center rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5';
/** Shown instead of a blank page when something in the app throws: try again, reload, or copy a report to send. */
export function ErrorScreen({ error, reset }) {
    const message = error instanceof Error ? error.message : String(error);
    const copy = async () => {
        const report = buildReport({ error, url: window.location.href, userAgent: navigator.userAgent, recent: recentErrors() });
        try {
            await navigator.clipboard.writeText(report);
            toast('Report copied');
        }
        catch {
            toast.error('Could not copy. Select the details below instead.');
        }
    };
    return (_jsxs("div", { role: "alert", className: "mx-auto grid max-w-lg justify-items-center gap-3 px-4 py-24 text-center", children: [_jsx("span", { className: "grid size-14 place-items-center rounded-full bg-bad/15 text-bad", children: _jsx(Icon, { name: "alert", className: "size-6" }) }), _jsx("h1", { className: "text-xl font-bold", children: "Something went wrong" }), _jsx("p", { className: "text-sm text-muted [overflow-wrap:anywhere]", children: "This page hit an unexpected problem. Your library and settings are safe." }), _jsx("p", { className: "max-w-full rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted [overflow-wrap:anywhere]", children: message }), _jsxs("div", { className: "flex flex-wrap justify-center gap-2", children: [reset ? _jsx("button", { type: "button", onClick: isStaleChunk(error) ? () => window.location.reload() : reset, className: `${BTN} bg-accent text-accent-fg`, children: "Try again" }) : null, _jsx("button", { type: "button", onClick: () => window.location.reload(), className: BTN, children: "Reload" }), _jsx("button", { type: "button", onClick: () => void copy(), className: BTN, children: "Copy report" })] })] }));
}
/** Catches errors thrown while rendering its children. `resetKey` (such as the current page) clears a caught error when it changes. */
export class ErrorBoundary extends Component {
    state = { error: null, failed: false };
    static getDerivedStateFromError(error) { return { error, failed: true }; }
    componentDidUpdate(prev) { if (this.state.failed && prev.resetKey !== this.props.resetKey)
        this.setState({ error: null, failed: false }); }
    render() { return this.state.failed ? _jsx(ErrorScreen, { error: this.state.error, reset: () => this.setState({ error: null, failed: false }) }) : this.props.children; }
}
