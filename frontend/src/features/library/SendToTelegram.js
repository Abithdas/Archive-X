import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { queryKeys, useChannels } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { ChannelPicker, SINGLE_CAPTION_LABEL } from './ChannelPicker';
import { channelName, directTarget, isAlreadySent, isDisplayName, noChannelMessage, sendBody, sentMessage } from './send';
/**
 * Send one video to a Telegram channel. With "Always send to this channel" saved it sends at once;
 * otherwise it asks which channel, shows the caption, and asks again before re-sending a duplicate.
 * Mounted by the card menu only while a send is in progress, so cards that never send cost nothing.
 */
export function SendToTelegram({ item, onDone }) {
    const qc = useQueryClient();
    const channels = useChannels();
    const list = channels.data ?? [];
    const [mode, setMode] = useState({ kind: 'resolving' });
    const [q, setQ] = useState('');
    const [target, setTarget] = useState('');
    const [remember, setRemember] = useState(false);
    const [caption, setCaption] = useState(null);
    const [busy, setBusy] = useState(false);
    // Read the cache, not the render's `list`: this runs from a closure created before the channels had loaded.
    const nameOf = (id) => channelName(qc.getQueryData(queryKeys.channels) ?? [], id);
    const send = async (to, rememberIt, force) => {
        const name = nameOf(to);
        setBusy(true);
        const pending = toast.loading(`Sending to ${name}…`);
        try {
            const r = await api('/api/send', { method: 'POST', json: sendBody(item, to, rememberIt, force) });
            toast.success(sentMessage(isDisplayName(r.target_title) ? String(r.target_title) : name, r.notes), { id: pending });
            setMode({ kind: 'closed' });
            void qc.invalidateQueries({ queryKey: ['library'] });
            void qc.invalidateQueries({ queryKey: ['channels'] });
            if (rememberIt)
                void qc.invalidateQueries({ queryKey: ['settings', 'send'] });
        }
        catch (e) {
            if (e instanceof ApiError && isAlreadySent(e.status)) {
                toast.dismiss(pending);
                setMode({ kind: 'again', target: to });
            }
            else {
                toast.error(`Send failed: ${e instanceof ApiError ? e.detail || 'unknown error' : 'unknown error'}`, { id: pending });
                setMode((m) => (m.kind === 'pick' ? m : { kind: 'closed' })); // keep the picker open so the user can retry
            }
        }
        finally {
            setBusy(false);
        }
    };
    const begun = useRef(false);
    const start = async () => {
        let c = {};
        try {
            await qc.ensureQueryData({ queryKey: queryKeys.channels, queryFn: () => api('/api/channels'), staleTime: 30_000 });
        }
        catch { /* names fall back to ids */ }
        try {
            c = await api('/api/settings/send');
        }
        catch { /* fall back to the picker */ }
        const direct = directTarget(c);
        if (direct)
            return void send(direct, false, false);
        setQ('');
        setRemember(false);
        setTarget(c.target ?? '');
        setCaption(null);
        setMode({ kind: 'pick' });
    };
    useEffect(() => { if (!begun.current) {
        begun.current = true;
        void start();
    } }); // once, on mount
    useEffect(() => { if (mode.kind === 'closed')
        onDone(); }, [mode.kind, onDone]);
    useEffect(() => {
        if (mode.kind !== 'pick')
            return;
        let live = true;
        api('/api/send/preview', { method: 'POST', json: { channel_id: item._cid, message_id: item.id } })
            .then((r) => { if (live)
            setCaption(r.caption ?? ''); })
            .catch(() => { if (live)
            setCaption(''); });
        return () => { live = false; };
    }, [mode.kind, item._cid, item.id]);
    return (_jsxs(_Fragment, { children: [mode.kind === 'pick' ? (_jsx(Dialog, { open: true, onClose: () => !busy && setMode({ kind: 'closed' }), title: "Send to Telegram", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-3", children: [_jsx(ChannelPicker, { channels: list, q: q, onQ: setQ, target: target, onTarget: setTarget, caption: caption, captionLabel: SINGLE_CAPTION_LABEL, remember: remember, onRemember: setRemember }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", disabled: busy, onClick: () => setMode({ kind: 'closed' }), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsxs("button", { type: "button", disabled: busy, onClick: () => (target ? void send(target, remember, false) : toast(noChannelMessage)), className: "flex h-11 items-center gap-2 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60", children: [_jsx(Icon, { name: "brand-telegram", className: "size-4" }), "Send"] })] })] }) })) : null, mode.kind === 'again' ? (_jsx(Dialog, { open: true, onClose: () => setMode({ kind: 'closed' }), title: "Video already sent", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsxs("p", { className: "text-sm text-muted [overflow-wrap:anywhere]", children: ["This video was already sent to ", nameOf(mode.target), ". Send it again?"] }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", disabled: busy, onClick: () => { setMode({ kind: 'closed' }); toast('Not sent.'); }, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", disabled: busy, onClick: () => void send(mode.target, false, true), className: "h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60", children: "Send again" })] })] }) })) : null] }));
}
