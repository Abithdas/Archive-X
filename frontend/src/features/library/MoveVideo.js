import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api, thumbUrl } from '@/api/client';
import { useChannels } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { formatBytes } from '@/design/format';
import { moveBody, moveConfirmText, moveMeta, moveResultMessage, optionLabel, resolveDestinations } from './move';
import { withWidth } from '@/design/responsive';
/**
 * "Move / delete source": copy one video to another Telegram channel, let the server verify the copy,
 * and only then delete the original. Mounted by the card menu while the dialog is open.
 */
export function MoveVideo({ item, onDone }) {
    const qc = useQueryClient();
    const published = useChannels();
    const inventory = useQuery({ queryKey: ['source-channels'], queryFn: () => api('/api/source-channels'), staleTime: 0, gcTime: 0, retry: false });
    const [dest, setDest] = useState('');
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState(false);
    const loading = inventory.isPending || inventory.isFetching;
    const failure = inventory.isError ? (inventory.error instanceof ApiError ? inventory.error.detail || 'request failed' : 'request failed') : '';
    const d = resolveDestinations(String(item._cid), inventory.data, published.data, failure);
    const chosen = d.options.find((c) => String(c.telegram_id) === dest);
    const sourceTitle = item.channel_title || 'Channel';
    const run = async () => {
        if (!chosen)
            return;
        setConfirm(false);
        setBusy(true);
        try {
            const r = await api('/api/library/move-delete', { method: 'POST', json: moveBody(item, chosen.telegram_id) });
            if (!r?.ok)
                throw new ApiError(0, 'Move was not completed');
            toast(moveResultMessage(r), { duration: 7000 });
            void qc.invalidateQueries({ queryKey: ['library'] });
            void qc.invalidateQueries({ queryKey: ['channels'] });
            onDone();
        }
        catch (e) {
            toast.error(e instanceof ApiError ? e.detail || 'Move failed; check the transfer log.' : 'Move failed; check the transfer log.', { duration: 7000 });
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs(_Fragment, { children: [_jsx(Dialog, { open: true, onClose: () => !busy && onDone(), title: "Move video", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsxs("div", { className: "flex gap-3 rounded-xl border border-line bg-bg p-2.5", children: [_jsx("img", { src: withWidth(thumbUrl(item._cid, item.thumbnail_id), 240), alt: "", className: "h-14 w-24 shrink-0 rounded-lg bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), _jsxs("div", { className: "min-w-0 text-sm", children: [_jsx("p", { className: "line-clamp-2 font-bold [overflow-wrap:anywhere]", children: item.name }), _jsx("p", { className: "truncate text-muted", children: moveMeta(sourceTitle, Number(item.size) || 0, formatBytes) })] })] }), _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Destination channel", _jsxs("select", { value: dest, onChange: (e) => setDest(e.target.value), disabled: loading || !d.options.length, className: "h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3 text-sm font-medium text-fg disabled:opacity-60", children: [_jsx("option", { value: "", children: loading ? 'Loading Telegram channels…' : 'Choose channel…' }), d.options.map((c) => _jsx("option", { value: String(c.telegram_id), children: optionLabel(c) }, c.telegram_id))] })] }), _jsx("p", { role: "status", "aria-live": "polite", className: d.state === 'warning' && !loading ? 'text-xs text-warn' : 'text-xs text-muted', children: loading ? 'Loading channels available to your Telegram account…' : d.message }), !loading && !d.options.length ? _jsx("button", { type: "button", onClick: () => void inventory.refetch(), className: "h-10 justify-self-start rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Refresh Telegram channels" }) : null, _jsxs("div", { role: "note", className: "flex gap-3 rounded-xl border border-line bg-bg p-3 text-xs text-muted", children: [_jsx("span", { className: "mt-0.5 text-good", children: _jsx(Icon, { name: "check", className: "size-4" }) }), _jsx("p", { children: "Archive X copies the video and verifies the new message and file before deleting the original. If verification fails, the original stays in place." })] }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", disabled: busy, onClick: onDone, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", disabled: busy || loading || !d.options.length, onClick: () => (chosen ? setConfirm(true) : toast('Choose a destination channel first.')), className: "h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60", children: busy ? 'Verifying destination…' : 'Move and delete source' })] })] }) }), confirm && chosen ? (_jsx(Dialog, { open: true, onClose: () => setConfirm(false), title: "Move video and delete source", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted [overflow-wrap:anywhere]", children: moveConfirmText(item.name, optionLabel(chosen)) }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setConfirm(false), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: () => void run(), className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white", children: "Move and delete source" })] })] }) })) : null] }));
}
