import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { dropdownPosition } from '@/design/placement';
import { useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api, thumbUrl } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { MappingDialog } from './MappingDialog';
import { MoveVideo } from './MoveVideo';
import { SendToTelegram } from './SendToTelegram';
import { withWidth } from '@/design/responsive';
const MSG = (e, fallback) => (e instanceof ApiError ? e.detail || fallback : fallback);
/** Asks the backend to open the video in a desktop player on this PC. */
export async function openExternal(item, player) {
    const r = await api('/api/play/external', { method: 'POST', json: { channel_id: item._cid, message_id: item.id, player } });
    if (!r?.ok)
        throw new ApiError(0, r?.detail || 'Player not found');
    return r;
}
/** Permanently deletes the Telegram message and its library entry (the backend verifies a linked thumbnail before removing it). */
export const deleteVideo = (item) => api('/api/library/delete', { method: 'POST', json: { channel_id: Number(item._cid), message_id: item.id, confirm_delete: true } }).then((r) => {
    if (!r?.ok)
        throw new ApiError(0, r?.detail || 'Video was not deleted');
});
const OPTION = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none';
/** The card's ⋯ menu. Light-dismiss, Esc and top-layer come from the native popover attribute. */
export function CardMenu({ item, className }) {
    const id = useId();
    const pop = useRef(null);
    const btn = useRef(null);
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState(false);
    const qc = useQueryClient();
    const [sending, setSending] = useState(false);
    const [moving, setMoving] = useState(false);
    const [mapping, setMapping] = useState(false);
    const place = () => {
        const el = pop.current;
        const b = btn.current?.getBoundingClientRect();
        if (!el || !b)
            return;
        const { left, top, maxHeight } = dropdownPosition(b, btn.current, 232, el.offsetHeight || 260, 'right');
        el.style.left = `${left}px`;
        el.style.top = `${top}px`;
        el.style.maxHeight = `${maxHeight}px`;
        el.style.overflowY = 'auto';
    };
    const close = () => pop.current?.hidePopover?.();
    const play = async (p) => {
        close();
        try {
            const r = await openExternal(item, p);
            toast(`Opened in ${r.player ?? (p === 'vlc' ? 'VLC' : 'MPC-HC')}`);
        }
        catch (e) {
            toast.error(`Player error: ${MSG(e, 'not found')}`);
        }
    };
    const remove = async () => {
        setBusy(true);
        try {
            await deleteVideo(item);
            setConfirm(false);
            toast.success('Video deleted');
            await Promise.all([qc.invalidateQueries({ queryKey: ['library'] }), qc.invalidateQueries({ queryKey: ['channels'] })]);
        }
        catch (e) {
            toast.error(MSG(e, 'Video was not deleted'));
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs(_Fragment, { children: [_jsx("button", { ref: btn, type: "button", popoverTarget: id, "aria-label": "More actions", "aria-haspopup": "menu", onClick: place, className: cn('grid h-9 w-7 place-items-center rounded-lg text-muted hover:bg-fg/5 hover:text-fg', className), children: _jsxs("span", { className: "flex gap-[3px]", "aria-hidden": "true", children: [_jsx("i", { className: "size-1 rounded-full bg-current" }), _jsx("i", { className: "size-1 rounded-full bg-current" }), _jsx("i", { className: "size-1 rounded-full bg-current" })] }) }), _jsxs("div", { ref: pop, id: id, popover: "auto", role: "menu", "aria-label": "Video actions", onToggle: (e) => e.nativeEvent.newState === 'open' && (place(), pop.current?.querySelector('button')?.focus()), className: "m-0 w-[232px] rounded-[16px] border border-line-2 bg-surface p-2 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]", children: [_jsx("div", { className: "px-3 pb-1 pt-1.5 text-xs font-semibold text-faint", "aria-hidden": "true", children: "Video actions" }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, title: "Copy to another Telegram channel, verify the copy, then delete source", onClick: () => { close(); setMoving(true); }, children: [_jsx(Icon, { name: "transfer", className: "text-muted" }), "Move / delete source"] }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => { close(); setSending(true); }, children: [_jsx(Icon, { name: "brand-telegram", className: "text-muted" }), "Send to Telegram"] }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => { close(); setMapping(true); }, children: [_jsx(Icon, { name: "edit", className: "text-muted" }), "Mapping / report"] }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => void play('vlc'), children: [_jsx(Icon, { name: "brand-vlc", className: "text-muted" }), "Open in VLC"] }), _jsxs("button", { type: "button", role: "menuitem", className: OPTION, onClick: () => void play('mpc'), children: [_jsx(Icon, { name: "play", className: "text-muted" }), "Open in MPC-HC"] }), _jsx("div", { role: "separator", className: "my-1.5 h-px bg-line" }), _jsxs("button", { type: "button", role: "menuitem", className: cn(OPTION, 'text-bad'), title: "Permanently delete from Telegram and remove from the Archive X Library", onClick: () => { close(); setConfirm(true); }, children: [_jsx(Icon, { name: "trash" }), "Delete"] })] }), mapping ? _jsx(MappingDialog, { item: item, onDone: () => setMapping(false) }) : null, moving ? _jsx(MoveVideo, { item: item, onDone: () => setMoving(false) }) : null, sending ? _jsx(SendToTelegram, { item: item, onDone: () => setSending(false) }) : null, confirm ? (_jsx(Dialog, { open: true, onClose: () => !busy && setConfirm(false), title: "Delete this video from Telegram?", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsxs("div", { className: "flex gap-3 rounded-xl border border-line bg-bg p-2.5", children: [_jsx("img", { src: withWidth(thumbUrl(item._cid, item.thumbnail_id), 240), alt: "", className: "h-14 w-24 shrink-0 rounded-lg bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), _jsxs("div", { className: "min-w-0 text-sm", children: [_jsx("p", { className: "line-clamp-2 font-bold [overflow-wrap:anywhere]", children: item.name }), _jsxs("p", { className: "truncate text-muted", children: [item.channel_title ?? 'Channel', " \u00B7 Telegram message ", item.id] })] })] }), _jsx("p", { className: "text-sm text-muted", children: "This permanently deletes the message from its Telegram channel and removes it from your Archive X Library. A linked Telegram thumbnail is deleted too only if it is verified as an image used exclusively by this video." }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", disabled: busy, onClick: () => setConfirm(false), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", disabled: busy, onClick: () => void remove(), className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white disabled:opacity-60", children: busy ? 'Deleting…' : 'Delete video' })] })] }) })) : null] }));
}
