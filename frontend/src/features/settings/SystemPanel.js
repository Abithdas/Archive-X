import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { ApiError, api } from '@/api/client';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { useHardware } from './queries';
import { browserRows, hardwareRows, speedResult } from './system';
const BOX = 'rounded-[18px] border border-line bg-surface p-5';
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-10 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60';
const Rows = ({ rows, label }) => (_jsx("dl", { "aria-label": label, className: "grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-2 lg:grid-cols-3", children: rows.map((r) => _jsxs("div", { className: "min-w-0 rounded-xl bg-bg p-3.5", children: [_jsx("dt", { className: "text-xs text-muted", children: r.label }), _jsx("dd", { className: "mt-1 text-sm font-bold [overflow-wrap:anywhere]", children: r.value })] }, r.label)) }));
/** The browser's own capabilities, read from navigator and a probe <video> element. */
function useBrowserRows() {
    return useMemo(() => {
        let v;
        try {
            v = document.createElement('video');
        }
        catch { /* no DOM video */ }
        const can = (t) => Boolean(v?.canPlayType?.(t));
        return browserRows({
            ua: navigator.userAgent || '', platform: navigator.platform || '', touchPoints: navigator.maxTouchPoints || 0,
            mp4: can('video/mp4'), webm: can('video/webm'), hls: can('application/vnd.apple.mpegurl'),
            fetch: typeof fetch === 'function', abort: typeof AbortController === 'function', pointer: typeof PointerEvent !== 'undefined',
        });
    }, []);
}
/** System & network: browser compatibility, detected hardware and an on-demand internet speed test. */
export function SystemPanel() {
    const qc = useQueryClient();
    const hw = useHardware();
    const browser = useBrowserRows();
    const [speed, setSpeed] = useState({ state: 'idle', text: '' });
    const [refreshing, setRefreshing] = useState(false);
    const refresh = async () => {
        setRefreshing(true);
        try {
            await qc.invalidateQueries({ queryKey: ['settings', 'hardware'] });
        }
        finally {
            setRefreshing(false);
        }
    };
    const runSpeed = async () => {
        setSpeed({ state: 'running', text: 'Testing latency, download and upload…' });
        try {
            const r = await api('/api/system/internet-test', { method: 'POST', json: {} });
            if (r.ok === false)
                throw new ApiError(0, String(r.detail || r.error || 'test failed'));
            const result = speedResult(r);
            setSpeed({ state: 'done', text: result.status, result });
            void qc.invalidateQueries({ queryKey: ['settings', 'hardware'] }); // the test updates the network profile
        }
        catch (e) {
            setSpeed({ state: 'failed', text: 'Internet test failed', error: e instanceof ApiError ? e.detail || 'test failed' : String(e) });
        }
    };
    const hwError = hw.isError || (hw.data && hw.data.ok === false);
    return (_jsxs("div", { className: "grid gap-4", children: [_jsxs("section", { "aria-label": "Browser compatibility", className: BOX, children: [_jsxs("div", { className: "mb-4 flex items-center justify-between", children: [_jsxs("h3", { className: "flex items-center gap-3 text-xl font-extrabold", children: [_jsx("span", { className: "text-accent", children: _jsx(Icon, { name: "info", className: "size-5" }) }), "Browser compatibility"] }), _jsx("span", { className: "hidden text-xs text-faint sm:block", children: "Feature detection & fallback" })] }), _jsx(Rows, { rows: browser, label: "Browser capabilities" }), _jsx("p", { className: "mt-3 text-xs text-muted", children: "The player always uses the direct stream (no conversion)." })] }), _jsxs("section", { "aria-label": "System and network optimization", className: BOX, children: [_jsxs("div", { className: "mb-2 flex items-center justify-between", children: [_jsxs("h3", { className: "flex items-center gap-3 text-xl font-extrabold", children: [_jsx("span", { className: "text-accent", children: _jsx(Icon, { name: "gauge", className: "size-5" }) }), "System & Network Optimization"] }), _jsx("span", { className: "hidden text-xs text-faint sm:block", children: "Hardware-aware" })] }), _jsx("p", { className: "mb-4 text-sm text-muted", children: "CPU, GPU, RAM and storage are detected at startup. Hardware encoding is used when verified. Internet testing is on-demand." }), hwError ? _jsx("p", { role: "alert", className: "text-sm text-warn", children: "Hardware detection unavailable." })
                        : hw.isPending ? _jsx("p", { role: "status", className: "text-sm text-muted", children: "Detecting CPU/GPU/RAM/storage\u2026" })
                            : _jsx(Rows, { rows: hardwareRows(hw.data), label: "Hardware" }), speed.result ? (_jsxs("div", { className: "mt-4", children: [_jsx("p", { className: "mb-2 text-xs font-bold uppercase tracking-wide text-muted", children: "Internet speed" }), _jsx(Rows, { rows: speed.result.rows, label: "Internet speed" }), _jsx("p", { className: "mt-2 text-xs text-muted", children: speed.result.note })] })) : speed.state === 'failed' ? _jsx("p", { role: "alert", className: "mt-4 text-sm text-bad [overflow-wrap:anywhere]", children: speed.error }) : null, _jsxs("div", { className: "mt-4 flex flex-wrap items-center gap-2", children: [_jsx("button", { type: "button", onClick: () => void refresh(), disabled: refreshing, className: BTN, children: "Refresh hardware" }), _jsx("button", { type: "button", onClick: () => void runSpeed(), disabled: speed.state === 'running', className: PRIMARY, children: "Test internet speed" }), _jsx("span", { role: "status", "aria-live": "polite", className: cn('text-xs', speed.state === 'done' ? (speed.result?.ok ? 'text-good' : 'text-warn') : speed.state === 'failed' ? 'text-bad' : 'text-muted'), children: speed.text })] })] })] }));
}
