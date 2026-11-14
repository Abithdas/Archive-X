import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiError, api } from '@/api/client';
import { cn } from '@/design/cn';
import { STATE_LABEL, anyBusy, modelsSummary, sizeText, stateTone, testLine } from './ai';
const BTN = 'inline-flex h-9 items-center rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'inline-flex h-9 items-center rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50';
/** Every local AI model in one place: what it is, what it powers, whether it is ready, a one-click self-test, and download. */
export function ModelCatalog() {
    const qc = useQueryClient();
    const q = useQuery({ queryKey: ['ai-models'], queryFn: async () => { const r = await api('/api/ai/models'); if (!r || !Array.isArray(r.models))
            throw new ApiError(0, 'The model list is unavailable.'); return r; }, staleTime: 0, retry: false, refetchInterval: (query) => (anyBusy(query.state.data) ? 2000 : false) });
    const [results, setResults] = useState({});
    const [testing, setTesting] = useState('');
    const test = async (m) => {
        setTesting(m.id);
        setResults((r) => ({ ...r, [m.id]: 'Testing…' }));
        try {
            const res = await api(`/api/ai/models/${m.id}/test`, { method: 'POST', json: {} });
            setResults((r) => ({ ...r, [m.id]: testLine(res) }));
        }
        catch (e) {
            setResults((r) => ({ ...r, [m.id]: `Not working: ${e instanceof ApiError && e.detail ? e.detail : 'the test could not run.'}` }));
        }
        finally {
            setTesting('');
        }
    };
    const download = async (m) => {
        setResults((r) => ({ ...r, [m.id]: `Downloading about ${sizeText(m.size_mb)}…` }));
        try {
            await api(`/api/ai/models/${m.id}/install`, { method: 'POST', json: {} });
        }
        catch (e) {
            setResults((r) => ({ ...r, [m.id]: e instanceof ApiError && e.detail ? e.detail : 'Could not start the download.' }));
        }
        await qc.invalidateQueries({ queryKey: ['ai-models'] });
    };
    const d = q.data;
    return (_jsxs("section", { "aria-label": "Local AI models", className: "rounded-2xl border border-line bg-surface p-4", children: [_jsxs("div", { className: "mb-3 flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("h2", { className: "text-lg font-bold", children: "Local AI models" }), _jsx("p", { className: "text-xs text-muted", children: "Everything runs on this PC. Nothing is sent to a cloud service." })] }), _jsx("span", { role: "status", "aria-live": "polite", className: "rounded-full bg-surface-2 px-3 py-1 text-xs font-bold", children: q.isError ? 'Could not check the models' : modelsSummary(d) })] }), q.isPending ? _jsx("p", { className: "text-sm text-muted", children: "Checking local models\u2026" }) : q.isError ? _jsx("button", { type: "button", className: PRIMARY, onClick: () => void q.refetch(), children: "Retry" }) : (_jsx("div", { className: "grid gap-3 lg:grid-cols-2", children: d.models.map((m) => {
                    const tone = stateTone(m.state);
                    return (_jsxs("article", { "aria-label": m.name, className: "grid content-start gap-2 rounded-xl border border-line bg-bg p-3 text-left", children: [_jsxs("div", { className: "flex items-start justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("b", { className: "block text-sm", children: m.name }), _jsx("small", { className: "text-xs text-muted", children: m.detail })] }), _jsx("span", { className: cn('shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-bold', tone === 'good' ? 'bg-good/15 text-good' : tone === 'busy' ? 'bg-accent/15 text-accent' : 'bg-warn/15 text-warn'), children: STATE_LABEL[m.state] })] }), _jsx("div", { className: "flex flex-wrap gap-1.5", "aria-label": `${m.name} is used by`, children: m.powers.map((p) => _jsx("span", { className: "rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted", children: p }, p)) }), _jsxs("p", { className: "text-xs text-faint", children: [sizeText(m.size_mb), " \u00B7 ", m.licence] }), m.hint && m.state !== 'ready' ? _jsx("p", { className: "text-xs text-muted", children: m.hint }) : null, m.error ? _jsxs("p", { role: "alert", className: "text-xs text-bad", children: ["Last download failed: ", m.error] }) : null, _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: testing === m.id || m.state === 'downloading', onClick: () => void test(m), children: "Test" }), m.installable ? _jsx("button", { type: "button", className: PRIMARY, disabled: m.state === 'downloading', onClick: () => void download(m), children: m.state === 'downloading' ? 'Downloading…' : 'Download' }) : null, _jsx("span", { role: "status", "aria-live": "polite", className: "min-w-0 text-xs text-muted", children: results[m.id] ?? '' })] })] }, m.id));
                }) }))] }));
}
