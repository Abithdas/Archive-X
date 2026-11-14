import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Icon } from '@/design/Icon';
import { SceneMatchReview } from './SceneMatchReview';
import { retryStatus, retryToast } from './scenematch';
/** The Operations card: open the review without scanning, or queue one manual provider recheck and open it. */
export function SceneMatchCard() {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [status, setStatus] = useState('');
    const retry = async () => {
        setBusy(true);
        try {
            const r = await api('/api/metadata/scene-match-review/retry-unmatched', { method: 'POST', json: {} });
            if (r?.ok) {
                setStatus(retryStatus(r));
                toast(retryToast(r));
            }
            else
                setStatus(r?.detail || 'Could not queue unmatched recheck.');
            setOpen(true);
        }
        catch (e) {
            setStatus(`Retry failed: ${e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e)}`);
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs("div", { className: "flex flex-col rounded-xl border border-line bg-bg p-4", children: [_jsx("h5", { className: "text-[15px] font-bold", children: "Scene Match Review & Retry" }), _jsxs("p", { className: "mb-3 mt-1 text-xs leading-relaxed text-muted", children: [_jsx("strong", { className: "text-fg", children: "Review missing metadata or recheck unmatched videos." }), " Open the review without starting a scan, or queue one manual provider recheck. Approving or rejecting candidates remains a separate action."] }), _jsxs("div", { className: "mt-auto flex flex-wrap gap-2", children: [_jsx("button", { type: "button", onClick: () => setOpen(true), title: "Open saved suggestions and missing metadata without rechecking", className: "inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5", children: "Open review" }), _jsxs("button", { type: "button", disabled: busy, onClick: () => void retry(), title: "Queue one manual recheck and open the scene-match review", className: "inline-flex h-9 items-center gap-2 rounded-xl bg-accent px-3 text-[13px] font-bold text-accent-fg disabled:opacity-60", children: [_jsx(Icon, { name: "refresh", className: "size-4" }), busy ? 'Queueing recheck…' : 'Retry & review'] })] }), status ? _jsx("p", { role: "status", "aria-live": "polite", className: "mt-2 text-xs text-muted", children: status }) : null, open ? _jsx(SceneMatchReview, { onClose: () => setOpen(false) }) : null] }));
}
