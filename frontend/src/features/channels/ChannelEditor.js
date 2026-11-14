import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Confirm } from '@/design/Confirm';
import { Dialog } from '@/design/Dialog';
import { Skeleton } from '@/design/Skeleton';
import { INTRO, LOCAL_ONLY, NEED_NAME, NOT_FOUND, READY, RESET_DONE, RESET_TEXT, SAVE_FAILED, SAVING, TITLE_MAX, chosenText, editBody, initialsOf, nothingToSave, photoProblem, previewUrl, resetPreviewUrl, shouldAskSync, startTitle, syncBody, syncChanges, syncCopy, syncDone, syncFailed } from './channelEdit';
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
/** Loads the channel, then edits it. */
export function ChannelEditor({ id, onClose }) {
    const q = useQuery({ queryKey: ['channel-edit', id], queryFn: () => api(`/api/channels/${encodeURIComponent(id)}/edit`), retry: false, staleTime: 0, gcTime: 0 });
    if (q.isPending)
        return _jsx(Dialog, { open: true, onClose: onClose, title: "Edit Telegram channel", children: _jsx(Skeleton, { className: "h-40 w-full rounded-xl" }) });
    if (q.isError || String(q.data?.telegram_id ?? '') !== id)
        return _jsx(Dialog, { open: true, onClose: onClose, title: "Edit Telegram channel", children: _jsx("p", { role: "alert", className: "text-sm text-bad", children: q.error instanceof ApiError && q.error.detail ? q.error.detail : NOT_FOUND }) });
    return _jsx(Form, { channel: q.data, onClose: onClose });
}
function Form({ channel, onClose }) {
    const qc = useQueryClient();
    const id = String(channel.telegram_id);
    const original = String(channel.title ?? '');
    const [title, setTitle] = useState(startTitle(channel));
    const [image, setImage] = useState('');
    const [reset, setReset] = useState(false);
    const [preview, setPreview] = useState(previewUrl(channel));
    const [previewOk, setPreviewOk] = useState(true);
    const [msg, setMsg] = useState({ text: READY, bad: false });
    const [busy, setBusy] = useState(false);
    const [ask, setAsk] = useState(null);
    const file = useRef(null);
    const refresh = () => Promise.all(['channels', 'photo-channels', 'source-channels'].map((k) => qc.invalidateQueries({ queryKey: [k] })));
    const choose = (f) => {
        if (!f)
            return;
        const p = photoProblem(f);
        if (p) {
            if (file.current)
                file.current.value = '';
            return void setMsg({ text: p, bad: true });
        }
        const r = new FileReader();
        r.onload = () => { const data = String(r.result || ''); setImage(data); setReset(false); setPreview(data); setPreviewOk(true); setMsg({ text: chosenText(f.name), bad: false }); };
        r.onerror = () => setMsg({ text: 'Could not read the selected image.', bad: true });
        r.readAsDataURL(f);
    };
    const useTelegramPhoto = () => { setImage(''); setReset(true); if (file.current)
        file.current.value = ''; setPreview(resetPreviewUrl(id)); setPreviewOk(true); setMsg({ text: RESET_TEXT, bad: false }); };
    const save = async () => {
        const draft = { title, original, imageData: image, reset };
        if (!title.trim())
            return void setMsg({ text: NEED_NAME, bad: true });
        if (nothingToSave(draft))
            return void onClose();
        setBusy(true);
        setMsg({ text: SAVING, bad: false });
        try {
            const r = await api(`/api/channels/${encodeURIComponent(id)}/edit`, { method: 'POST', json: editBody(draft) });
            if (!r || r.ok === false)
                throw new ApiError(0, r?.detail || SAVE_FAILED);
            await refresh();
            if (shouldAskSync(draft))
                setAsk(draft);
            else {
                toast(RESET_DONE);
                onClose();
            }
        }
        catch (e) {
            setMsg({ text: e instanceof ApiError && e.detail ? e.detail : SAVE_FAILED, bad: true });
            setBusy(false);
        }
    };
    const push = async (d) => {
        setAsk(null);
        try {
            const r = await api(`/api/channels/${encodeURIComponent(id)}/sync-to-telegram`, { method: 'POST', json: syncBody(d) });
            if (!r || r.ok === false)
                throw new ApiError(0, r?.detail || r?.error || '');
            await refresh();
            toast(syncDone(r.synced));
        }
        catch (e) {
            toast(syncFailed(e instanceof ApiError ? e.detail : ''));
        }
        onClose();
    };
    const copy = ask ? syncCopy(syncChanges(ask)) : null;
    return (_jsxs(_Fragment, { children: [_jsx(Dialog, { open: true, onClose: () => !busy && !ask && onClose(), title: "Edit Telegram channel", children: _jsxs("div", { className: "grid gap-4 text-left", children: [_jsx("p", { className: "text-sm text-muted", children: INTRO }), _jsxs("label", { className: "grid gap-1 text-xs font-semibold text-muted", children: ["Channel name", _jsx("input", { value: title, maxLength: TITLE_MAX, autoComplete: "off", placeholder: "Enter channel name", onChange: (e) => setTitle(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') {
                                        e.preventDefault();
                                        void save();
                                    } }, className: "h-11 rounded-xl border border-line-2 bg-bg px-3 text-sm font-normal text-fg" })] }), _jsxs("section", { "aria-label": "Channel profile photo", className: "flex items-center gap-4", children: [_jsxs("span", { className: "relative grid size-20 shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 text-xl font-bold", children: [_jsx("span", { "aria-hidden": "true", children: initialsOf(title) }), previewOk ? _jsx("img", { src: preview, alt: "Channel profile photo preview", className: "absolute inset-0 size-full object-cover", onError: () => setPreviewOk(false) }, preview) : null] }), _jsxs("div", { className: "grid gap-1.5", children: [_jsx("strong", { className: "text-sm", children: "Profile photo" }), _jsx("span", { className: "text-xs text-muted", children: "PNG, JPEG, WebP or GIF \u00B7 maximum 8 MiB" }), _jsx("input", { ref: file, type: "file", accept: "image/png,image/jpeg,image/webp,image/gif", hidden: true, "aria-label": "Choose a photo file", onChange: (e) => choose(e.target.files?.[0]) }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => file.current?.click(), children: "Choose photo\u2026" }), _jsx("button", { type: "button", className: BTN, disabled: busy, onClick: useTelegramPhoto, children: "Use Telegram photo" })] })] })] }), _jsx("p", { role: "status", "aria-live": "polite", className: msg.bad ? 'text-sm text-bad' : 'text-sm text-muted', children: msg.text }), _jsxs("div", { className: "flex justify-end gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: busy, onClick: onClose, children: "Cancel" }), _jsx("button", { type: "button", className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60", disabled: busy, onClick: () => void save(), children: "Save changes" })] })] }) }), ask && copy ? _jsx(Confirm, { title: copy.title, text: copy.text, ok: copy.ok, cancel: copy.cancel, danger: true, onCancel: () => { setAsk(null); toast(LOCAL_ONLY); onClose(); }, onOk: () => void push(ask) }) : null] }));
}
