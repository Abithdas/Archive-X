import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { queryKeys } from '@/api/hooks';
import { Confirm } from '@/design/Confirm';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { FALLBACK_NOTE, NOT_WAITING, activityText, jobLogUrl, STOP_COPY, answerText, appendLines, consoleTitle, jobLine, liveOperation, nextDelay, panelFor, percent, pollUrl } from './console';
/** Live view of the Telegram job manager: its output, current operation, any question it is asking, and Stop. */
export function TransferConsole({ onClose, jobId }) {
    const qc = useQueryClient();
    const [state, setState] = useState(null);
    const [text, setText] = useState('');
    const [answer, setAnswer] = useState('');
    const [sending, setSending] = useState(false);
    const [ask, setAsk] = useState(false);
    const [stopping, setStopping] = useState(false);
    const seq = useRef(0);
    const log = useRef('');
    const alive = useRef(true);
    const timer = useRef(undefined);
    const scroller = useRef(null);
    const atEnd = useRef(true);
    const poll = useRef(async () => undefined);
    poll.current = async () => {
        clearTimeout(timer.current);
        let running = false;
        try {
            const r = await api(pollUrl(seq.current));
            if (!alive.current)
                return;
            const m = appendLines(log.current, seq.current, r);
            log.current = m.text;
            seq.current = m.seq;
            setText(m.text);
            setState(r);
            running = Boolean(r.running);
        }
        catch { /* keep the last view and try again */ }
        if (alive.current)
            timer.current = setTimeout(() => void poll.current(), nextDelay(running));
    };
    useEffect(() => { alive.current = true; void poll.current(); return () => { alive.current = false; clearTimeout(timer.current); }; }, []);
    const send = async () => {
        const v = answerText(answer);
        if (!v || sending)
            return;
        setSending(true);
        try {
            const r = await api('/api/telegram-transfer/input', { method: 'POST', json: { text: v } });
            if (!r?.ok) {
                toast(r?.detail || NOT_WAITING);
                return;
            }
            setAnswer('');
        }
        catch (e) {
            toast(e instanceof ApiError && e.detail ? e.detail : NOT_WAITING);
        }
        finally {
            setSending(false);
            void poll.current();
        }
    };
    const stop = async () => {
        setStopping(true);
        try {
            await api('/api/telegram-transfer/stop', { method: 'POST' });
        }
        catch (e) {
            toast(e instanceof ApiError && e.detail ? e.detail : 'Could not stop the Telegram job.');
        }
        finally {
            setStopping(false);
            void poll.current();
            void qc.invalidateQueries({ queryKey: queryKeys.status });
        }
    };
    const noCli = state !== null && !text;
    const jobLog = useQuery({ queryKey: ['job-log', jobId], enabled: Boolean(jobId) && noCli, refetchInterval: 2500, retry: false, queryFn: () => api(jobLogUrl(jobId)) });
    const fallback = noCli && jobId ? activityText(jobLog.data?.activity ?? [], (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString(); }) : '';
    const shown = text || fallback;
    useEffect(() => { const el = scroller.current; if (el && atEnd.current)
        el.scrollTop = el.scrollHeight; }, [text, fallback]);
    const s = state ?? {};
    const panel = panelFor(s);
    const pct = percent(s);
    return (_jsxs(_Fragment, { children: [_jsx(Dialog, { wide: true, open: true, onClose: onClose, title: consoleTitle(s), actions: s.running ? _jsxs("button", { type: "button", disabled: stopping, onClick: () => setAsk(true), className: "inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-2 px-3 text-xs font-bold text-bad hover:bg-fg/5", children: [_jsx(Icon, { name: "stop", className: "size-3.5" }), "Stop"] }) : null, children: _jsxs("div", { className: "grid gap-3 text-left", children: [_jsx("p", { role: "status", "aria-live": "polite", className: "text-sm text-muted", children: state ? jobLine(s) : 'Connecting…' }), s.running ? (_jsxs("div", { className: "rounded-xl border border-line p-3", "aria-live": "polite", children: [_jsxs("div", { className: "text-sm", children: [_jsx("b", { children: "LIVE" }), " \u00B7 ", liveOperation(s)] }), _jsx("div", { className: "mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3", role: "progressbar", "aria-label": "Progress", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct ?? undefined, children: _jsx("span", { className: `block h-full rounded-full bg-accent ${pct === null ? 'w-1/3 animate-pulse' : ''}`, style: pct === null ? undefined : { width: `${pct}%` } }) })] })) : null, panel.kind === 'prompt' ? (_jsxs("div", { className: "rounded-xl border border-line bg-bg p-3", children: [_jsxs("div", { className: "mb-1 flex items-center gap-2 text-xs font-bold text-muted", children: [_jsx("span", { "aria-hidden": "true", className: "size-2 rounded-full bg-good" }), "Interactive Media Manager CLI", _jsx("span", { className: "font-normal", children: "Enter the response requested by the CLI." })] }), _jsx("p", { className: "whitespace-pre-wrap text-sm [overflow-wrap:anywhere]", children: panel.body }), _jsxs("div", { className: "mt-2 flex gap-2", children: [_jsx("input", { value: answer, disabled: !panel.canAnswer || sending, onChange: (e) => setAnswer(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                                                e.preventDefault();
                                                void send();
                                            } }, placeholder: panel.placeholder, "aria-label": "Answer", autoComplete: "off", className: "h-10 min-w-0 flex-1 rounded-xl border border-line-2 bg-surface px-3 text-sm disabled:opacity-60" }), _jsx("button", { type: "button", disabled: !panel.canAnswer || sending || !answer.trim(), onClick: () => void send(), className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50", children: "Send" })] })] })) : _jsxs("div", { className: "rounded-xl border border-line p-3", children: [_jsx("b", { className: "text-sm", children: panel.title }), _jsx("p", { className: "mt-1 text-xs text-muted", children: panel.help })] }), _jsxs("div", { children: [_jsx("h3", { className: "mb-1 text-xs font-bold uppercase tracking-wide text-muted", children: "CLI output" }), !text && fallback ? _jsx("p", { className: "mb-1 text-[11px] text-muted", children: FALLBACK_NOTE }) : null, _jsx("pre", { ref: scroller, "aria-label": "CLI output", tabIndex: 0, onScroll: (e) => { const el = e.currentTarget; atEnd.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 40; }, className: "max-h-[40vh] min-h-32 overflow-auto whitespace-pre-wrap rounded-xl bg-black/40 p-3 font-mono text-xs [overflow-wrap:anywhere]", children: shown })] })] }) }), ask ? _jsx(Confirm, { title: STOP_COPY.title, text: STOP_COPY.text, ok: STOP_COPY.ok, danger: true, onCancel: () => setAsk(false), onOk: () => { setAsk(false); void stop(); } }) : null] }));
}
