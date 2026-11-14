import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useProfile } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { INTRO, NAME_MAX, READY, RESET_TEXT, SAVED, SAVE_FAILED, chosenText, displayName, initials, nameProblem, photoProblem, photoUrl, saveBody, unchanged } from './profile';
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
/** The account's round photo, or its initials when there is none. */
export function AccountAvatar({ className = 'size-10 text-[15px]' }) {
    const p = useProfile();
    const [broken, setBroken] = useState('');
    const url = photoUrl(p.data);
    const name = displayName(p.data) || 'A';
    return (_jsxs("span", { className: `relative grid shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 font-bold ${className}`, children: [_jsx("span", { "aria-hidden": "true", children: initials(name) }), broken !== url ? _jsx("img", { src: url, alt: "", className: "absolute inset-0 size-full object-cover", onError: () => setBroken(url) }) : null] }));
}
/** Edit the local display name and photo. */
export function ProfileEditor({ onClose }) {
    const qc = useQueryClient();
    const p = useProfile().data;
    const [name, setName] = useState(p?.custom_name || p?.name || '');
    const [image, setImage] = useState('');
    const [reset, setReset] = useState(false);
    const [preview, setPreview] = useState(photoUrl(p));
    const [msg, setMsg] = useState({ text: READY, bad: false });
    const [busy, setBusy] = useState(false);
    const file = useRef(null);
    const choose = (f) => {
        if (!f)
            return;
        const problem = photoProblem(f);
        if (problem) {
            if (file.current)
                file.current.value = '';
            return void setMsg({ text: problem, bad: true });
        }
        const reader = new FileReader();
        reader.onload = () => { const data = String(reader.result || ''); setImage(data); setReset(false); setPreview(data); setMsg({ text: chosenText(f.name), bad: false }); };
        reader.onerror = () => setMsg({ text: 'Could not read that image.', bad: true });
        reader.readAsDataURL(f);
    };
    const useTelegramPhoto = () => { setImage(''); setReset(true); if (file.current)
        file.current.value = ''; setPreview(`/api/telegram/me/photo?refresh=${Date.now()}`); setMsg({ text: RESET_TEXT, bad: false }); };
    const save = async () => {
        const draft = { name, imageData: image, reset };
        const bad = nameProblem(name);
        if (bad)
            return void setMsg({ text: bad, bad: true });
        if (unchanged(p, draft))
            return void onClose();
        setBusy(true);
        setMsg({ text: 'Saving…', bad: false });
        try {
            const r = await api('/api/account/profile', { method: 'POST', json: saveBody(draft) });
            if (!r || r.ok === false || r.detail)
                throw new ApiError(0, r?.detail || SAVE_FAILED);
            qc.setQueryData(['profile'], { ...r, image_version: r.image_version || String(Date.now()) });
            toast(SAVED);
            onClose();
        }
        catch (e) {
            setMsg({ text: e instanceof ApiError && e.detail ? e.detail : SAVE_FAILED, bad: true });
            setBusy(false);
        }
    };
    const [previewOk, setPreviewOk] = useState(true);
    return (_jsx(Dialog, { open: true, onClose: () => !busy && onClose(), title: "Edit profile", children: _jsxs("div", { className: "grid gap-4 text-left", children: [_jsx("p", { className: "text-sm text-muted", children: INTRO }), _jsxs("label", { className: "grid gap-1 text-xs font-semibold text-muted", children: ["Display name", _jsx("input", { value: name, maxLength: NAME_MAX, autoComplete: "off", placeholder: "Your name", onChange: (e) => setName(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') {
                                e.preventDefault();
                                void save();
                            } }, className: "h-11 rounded-xl border border-line-2 bg-bg px-3 text-sm font-normal text-fg" })] }), _jsxs("section", { "aria-label": "Profile photo", className: "flex items-center gap-4", children: [_jsxs("span", { className: "relative grid size-20 shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 text-2xl font-bold", children: [_jsx("span", { "aria-hidden": "true", children: initials(p?.name || name) }), previewOk ? _jsx("img", { src: preview, alt: "Profile photo preview", className: "absolute inset-0 size-full object-cover", onLoad: () => setPreviewOk(true), onError: () => setPreviewOk(false) }, preview) : null] }), _jsxs("div", { className: "grid gap-1.5", children: [_jsx("strong", { className: "text-sm", children: "Profile photo" }), _jsx("span", { className: "text-xs text-muted", children: "PNG, JPEG, WebP or GIF \u00B7 maximum 8 MiB" }), _jsx("input", { ref: file, type: "file", accept: "image/png,image/jpeg,image/webp,image/gif", hidden: true, onChange: (e) => choose(e.target.files?.[0]), "aria-label": "Choose a photo file" }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: busy, onClick: () => file.current?.click(), children: "Choose photo\u2026" }), _jsx("button", { type: "button", className: BTN, disabled: busy, onClick: useTelegramPhoto, children: "Use Telegram photo" })] })] })] }), _jsx("p", { role: "status", "aria-live": "polite", className: msg.bad ? 'text-sm text-bad' : 'text-sm text-muted', children: msg.text }), _jsxs("div", { className: "flex justify-end gap-2", children: [_jsx("button", { type: "button", className: BTN, disabled: busy, onClick: onClose, children: "Cancel" }), _jsx("button", { type: "button", className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60", disabled: busy, onClick: () => void save(), children: "Save changes" })] })] }) }));
}
