import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { TransferConsole } from './TransferConsole';
import { hasConsole } from './console';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { queryKeys } from '@/api/hooks';
import { Confirm } from '@/design/Confirm';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { PLAYBACK_STOPPING, PLAYBACK_UNKNOWN, READ_FAILED, REFRESH_FAILED, STOP_ERROR, alreadyFinished, controlsFor, doneMessage, errorFor, idOf, isTerminal, jobPath, playbackConfirm, playbackDone, primaryLabel, primaryPath, stopConfirm, stopDone } from './jobs';
const BTN = 'inline-flex h-8 min-w-0 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-line-2 px-2 text-xs font-bold hover:bg-fg/5 disabled:opacity-50';
/** The short words on the buttons; the full wording stays in the tooltip and the accessible name. */
const SHORT = { recover: 'Recover', 'retry-recovery': 'Retry', resume: 'Resume', pause: 'Pause' };
const reason = (e, fallback) => (e instanceof ApiError && e.detail ? e.detail : fallback);
const inFlight = new Set(); // one control request per job at a time
/** The behaviour behind the job buttons: pause / resume / recover, stop (with its pre-check and confirmation), refresh and stop playback. `onFinished` runs after an action that was actually carried out. */
export function useJobActions(j, onFinished) {
    const qc = useQueryClient();
    const [ask, setAsk] = useState(null);
    const [busy, setBusy] = useState(false);
    const c = controlsFor(j);
    const id = idOf(j);
    const refetch = () => Promise.all([qc.invalidateQueries({ queryKey: queryKeys.status }), qc.invalidateQueries({ queryKey: ['lanes'] })]);
    /** Runs one request for this job, never two at once, and refreshes the page data afterwards. */
    const run = async (fn) => {
        if (!id || inFlight.has(id))
            return;
        inFlight.add(id);
        setBusy(true);
        try {
            await fn();
        }
        finally {
            inFlight.delete(id);
            setBusy(false);
            void refetch();
        }
    };
    const post = (tail) => api(jobPath(id, tail), { method: 'POST' });
    const primary = (p) => run(async () => {
        try {
            const r = await post(primaryPath(p));
            if (r?.ok === false)
                return void toast(r.detail || errorFor(p));
            toast(doneMessage(p, r ?? {}));
            onFinished?.();
        }
        catch (e) {
            toast(reason(e, errorFor(p)));
        }
    });
    /** Stop first reads the job again: it may have finished since the list was drawn. */
    const stop = () => run(async () => {
        try {
            const cur = await api(jobPath(id));
            const st = String(cur.status ?? '').trim().toUpperCase();
            if (isTerminal(st))
                return void toast(alreadyFinished(st));
            setAsk({ kind: 'stop', st });
        }
        catch (e) {
            toast(reason(e, READ_FAILED));
        }
    });
    const confirmedStop = () => {
        setAsk(null);
        void run(async () => {
            try {
                const r = await post('/cancel');
                if (r?.ok === false)
                    return void toast(r.detail || STOP_ERROR);
                toast(stopDone(r ?? {}));
                onFinished?.();
            }
            catch (e) {
                toast(reason(e, STOP_ERROR));
            }
        });
    };
    const refresh = () => run(async () => {
        try {
            await api(jobPath(id));
        }
        catch (e) {
            toast(reason(e, REFRESH_FAILED));
        } // read-only; the list refetches afterwards
    });
    const confirmedPlayback = () => {
        setAsk(null);
        toast(PLAYBACK_STOPPING);
        void run(async () => {
            try {
                const r = await api('/api/play/stop', { method: 'POST' });
                toast(r?.detail ? r.detail : playbackDone(r ?? {}));
                onFinished?.();
            }
            catch (e) {
                toast(reason(e, PLAYBACK_UNKNOWN));
            }
        });
    };
    const dialogs = (_jsxs(_Fragment, { children: [ask?.kind === 'stop' ? (() => { const s = stopConfirm(ask.st); return _jsx(Confirm, { title: s.title, text: s.text, ok: s.ok, danger: true, onCancel: () => setAsk(null), onOk: confirmedStop }); })() : null, ask?.kind === 'playback' ? _jsx(Confirm, { title: playbackConfirm.title, text: playbackConfirm.text, ok: playbackConfirm.ok, danger: true, onCancel: () => setAsk(null), onOk: confirmedPlayback }) : null] }));
    return { c, busy, primary, stop, refresh, askPlayback: () => setAsk({ kind: 'playback' }), refetch, dialogs };
}
/** Pause / resume / recover, stop and refresh for one job, or just "Stop playback" for a playback session. */
export function JobControls({ j }) {
    const { c, busy, primary, stop, refresh, askPlayback, refetch, dialogs } = useJobActions(j);
    const [console_, setConsole] = useState(false);
    // Space for the buttons is always kept, so a job card does not change size when its state does (running -> finished and back).
    if (!c)
        return _jsx("div", { "aria-hidden": "true", className: "mt-auto min-h-8 pt-2.5" });
    return (_jsxs("div", { className: "mt-auto flex min-h-8 gap-1.5 pt-2.5", role: "group", "aria-label": `Controls for ${String(j.name || j.type || 'job')}`, children: [c.kind === 'playback' ? (_jsxs(_Fragment, { children: [_jsxs("button", { type: "button", disabled: busy, onClick: askPlayback, className: cn(BTN, 'text-bad'), children: [_jsx(Icon, { name: "stop", className: "size-3.5" }), "Stop playback"] }), _jsxs("button", { type: "button", disabled: busy, onClick: () => void refetch(), className: BTN, children: [_jsx(Icon, { name: "refresh", className: "size-3.5" }), "Refresh"] })] })) : (_jsxs(_Fragment, { children: [_jsxs("button", { type: "button", disabled: busy, onClick: () => void primary(c.primary), title: primaryLabel(c.primary), "aria-label": primaryLabel(c.primary), className: cn(BTN, (c.primary === 'recover' || c.primary === 'retry-recovery') && 'border-accent bg-accent text-accent-fg hover:bg-accent'), children: [_jsx(Icon, { name: c.primary === 'pause' ? 'pause' : 'play', className: "size-3.5 shrink-0" }), _jsx("span", { className: "truncate", children: SHORT[c.primary] })] }), _jsxs("button", { type: "button", disabled: busy, onClick: () => void stop(), className: cn(BTN, 'text-bad'), children: [_jsx(Icon, { name: "stop", className: "size-3.5" }), "Stop"] }), _jsxs("button", { type: "button", disabled: busy, onClick: () => void refresh(), className: BTN, children: [_jsx(Icon, { name: "refresh", className: "size-3.5" }), "Refresh"] }), hasConsole(j) ? _jsx("button", { type: "button", onClick: () => setConsole(true), title: "Open console", "aria-label": "Open console", className: BTN, children: _jsx("span", { className: "truncate", children: "Console" }) }) : null] })), console_ ? _jsx(TransferConsole, { jobId: idOf(j), onClose: () => setConsole(false) }) : null, dialogs] }));
}
