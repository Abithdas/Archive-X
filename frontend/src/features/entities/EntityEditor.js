import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { LOADING, STATE_LOCAL, STATE_RESET, candidatesUrl, editUrl, fileProblem, footerFor, groups, imageUrl, initialState, isPerformer, loadFailed, lookupFailed, nameProblem, providerSummary, saveBody, saveFailed, saveLabel, savedMessage, stateProvider, titleFor } from './editor';
import { normalizeGender } from './filter';
const INPUT = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60';
const PANEL = 'grid gap-2 rounded-2xl border border-line p-3';
/** Edit a performer or studio: picture (provider choice, URL, file or "use the provider's again"), name, and for performers a profile URL and biography. */
export function EntityEditor({ kind, id, onDone }) {
    const qc = useQueryClient();
    const [data, setData] = useState(null);
    const [name, setName] = useState('');
    const [profile, setProfile] = useState('');
    const [bio, setBio] = useState('');
    const [gender, setGender] = useState('');
    const [url, setUrl] = useState('');
    const [imageData, setImageData] = useState('');
    const [reset, setReset] = useState(false);
    const [picked, setPicked] = useState(null);
    const [preview, setPreview] = useState('');
    const [state, setState] = useState('');
    const [cands, setCands] = useState([]);
    const [hasMore, setHasMore] = useState(false);
    const [providerStatus, setProviderStatus] = useState(LOADING);
    const [footer, setFooter] = useState(LOADING);
    const [broken, setBroken] = useState(new Set());
    const [saving, setSaving] = useState(false);
    const [nameMsg, setNameMsg] = useState('');
    const file = useRef(null);
    const offset = useRef(0);
    const live = useRef(true);
    useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
    // The entity itself.
    useEffect(() => {
        api(editUrl(kind, id)).then((r) => {
            if (!live.current)
                return;
            setData(r);
            setName(r.name ?? '');
            setProfile(r.profile ?? '');
            setBio(r.bio ?? '');
            setGender(normalizeGender(r.gender));
            setUrl(r.image_source === 'manual' && /^https?:/i.test(r.image ?? '') ? String(r.image) : '');
            setPreview(imageUrl(kind, id, r.image));
            setState(initialState(Boolean(r.image_locked)));
        }).catch((e) => { toast.error(loadFailed(e instanceof ApiError ? e.detail : '')); onDone(); });
    }, [kind, id]); // eslint-disable-line react-hooks/exhaustive-deps
    // Provider images load by themselves when the editor opens, ten at a time.
    const loadCandidates = async (more) => {
        setProviderStatus(more ? 'Loading more provider images…' : LOADING);
        try {
            const r = await api(candidatesUrl(kind, id, more ? offset.current : 0));
            if (!live.current)
                return;
            const next = more ? [...cands, ...(r.candidates ?? [])] : (r.candidates ?? []);
            offset.current = (more ? offset.current : 0) + 10;
            setCands(next);
            setHasMore(Boolean(r.has_more));
            setProviderStatus(providerSummary(next, Boolean(r.has_more)));
            setFooter(footerFor(next.length));
        }
        catch (e) {
            if (!live.current)
                return;
            setProviderStatus(lookupFailed(e instanceof ApiError ? e.detail || 'provider lookup failed' : 'provider lookup failed'));
            setFooter('You can still use a manual image URL or file.');
        }
    };
    useEffect(() => { void loadCandidates(false); }, [kind, id]); // eslint-disable-line react-hooks/exhaustive-deps
    const choose = (c) => {
        setPicked(c);
        setImageData('');
        setReset(false);
        setUrl('');
        if (file.current)
            file.current.value = '';
        setPreview(c.url);
        setState(stateProvider(c.provider));
        setProviderStatus('Image selected.');
        setFooter('Selected image will be saved when you save.');
    };
    const upload = (f) => {
        if (!f)
            return;
        const bad = fileProblem(f.size);
        if (bad) {
            toast(bad);
            if (file.current)
                file.current.value = '';
            return;
        }
        setReset(false);
        setPicked(null);
        setUrl('');
        const reader = new FileReader();
        reader.onload = () => { setImageData(String(reader.result || '')); setPreview(String(reader.result || '')); setState(STATE_LOCAL); setFooter('Local image selected.'); };
        reader.readAsDataURL(f);
    };
    const restore = () => {
        setPicked(null);
        setImageData('');
        setReset(true);
        setUrl('');
        if (file.current)
            file.current.value = '';
        setPreview(`/api/${kind}/${id}/image?v=${Date.now()}`);
        setState(STATE_RESET);
        setFooter('Provider image restore selected.');
    };
    const typeUrl = (v) => { setUrl(v); setReset(false); setPicked(null); };
    const save = async (e) => {
        e.preventDefault();
        const bad = nameProblem(name);
        if (bad)
            return void setNameMsg(bad);
        const form = { kind, name, manualUrl: url, reset, profile, bio, gender, imageData, provider: picked };
        setSaving(true);
        try {
            const r = await api(editUrl(kind, id), { method: 'POST', json: saveBody(form) });
            if (r?.ok === false)
                throw new ApiError(0, r.detail || '');
            toast(savedMessage(form));
            void qc.invalidateQueries({ queryKey: ['entities'] });
            void qc.invalidateQueries({ queryKey: ['performer', id] });
            onDone();
        }
        catch (err) {
            toast.error(saveFailed(err instanceof ApiError ? err.detail : ''));
        }
        finally {
            setSaving(false);
        }
    };
    const grouped = groups(cands.filter((c) => !broken.has(c.url)));
    return (_jsx(Dialog, { wide: true, open: true, onClose: () => !saving && onDone(), title: titleFor(kind), children: _jsxs("form", { onSubmit: (e) => void save(e), className: "grid grid-cols-[minmax(0,1fr)] gap-4 text-left", "aria-busy": !data, children: [data ? _jsx("p", { className: "text-sm text-muted [overflow-wrap:anywhere]", children: data.name }) : _jsx("p", { role: "status", className: "text-sm text-muted", children: "Loading\u2026" }), _jsxs("section", { className: "grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]", "aria-label": "Profile picture", children: [_jsxs("div", { className: "grid content-start gap-2", children: [_jsx("p", { className: "text-xs font-semibold text-muted", children: "Selected preview" }), _jsx("div", { className: "aspect-[3/4] w-full max-w-[220px] overflow-hidden rounded-2xl border border-line bg-surface-2", children: preview ? _jsx("img", { src: preview, alt: "Profile picture preview", className: "size-full object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }) : null }), _jsx("p", { role: "status", className: "text-xs text-muted", children: state })] }), _jsxs("div", { className: "grid min-w-0 content-start gap-3", children: [_jsxs("div", { className: PANEL, children: [_jsx("h4", { className: "text-sm font-bold", children: "Provider images" }), _jsx("p", { role: "status", className: "text-xs text-muted", children: providerStatus }), _jsxs("div", { "aria-label": "Alternative API profile images", className: "grid gap-3", children: [grouped.length ? grouped.map((g) => (_jsxs("section", { className: "grid gap-1.5", children: [_jsxs("div", { className: "flex items-baseline justify-between text-xs", children: [_jsx("strong", { children: g.label }), _jsxs("span", { className: "text-muted", children: [g.items.length, " loaded"] })] }), _jsx("div", { className: "grid grid-cols-[repeat(auto-fill,minmax(88px,1fr))] gap-2", children: g.items.map(({ c, index, caption }) => (_jsxs("button", { type: "button", title: "Select this image", "aria-label": `${titleFor(kind)} option ${index + 1}: ${caption}`, "aria-pressed": picked?.url === c.url, onClick: () => choose(c), className: cn('relative aspect-[3/4] overflow-hidden rounded-xl border bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-accent', picked?.url === c.url ? 'border-accent ring-2 ring-accent/60' : c.current ? 'border-good' : 'border-line'), children: [_jsx("img", { src: c.url, alt: "", loading: "lazy", className: "size-full object-cover", onError: () => setBroken((s) => new Set(s).add(c.url)) }), _jsx("span", { className: "absolute inset-x-0 bottom-0 truncate bg-black/65 px-1 py-0.5 text-[10px] font-semibold text-white", children: caption })] }, c.url))) })] }, g.key))) : null, hasMore ? _jsx("button", { type: "button", onClick: () => void loadCandidates(true), className: "h-9 justify-self-start rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5", children: "Load more images" }) : null] })] }), _jsxs("div", { className: PANEL, children: [_jsx("h4", { className: "text-sm font-bold", children: "Manual image" }), _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Image URL", _jsx("input", { value: url, onChange: (e) => typeUrl(e.target.value), placeholder: "https://\u2026", className: INPUT, inputMode: "url" })] }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsxs("label", { className: "inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 focus-within:ring-2 focus-within:ring-accent", children: [_jsx(Icon, { name: "upload", className: "size-4" }), "Choose from this PC", _jsx("input", { ref: file, type: "file", accept: "image/png,image/jpeg,image/webp,image/gif,image/svg+xml", onChange: (e) => upload(e.target.files?.[0]), className: "sr-only", "aria-label": "Choose an image from this PC" })] }), _jsxs("button", { type: "button", onClick: restore, className: "inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5", children: [_jsx(Icon, { name: "refresh", className: "size-4" }), "Use provider image again"] })] })] })] })] }), _jsxs("details", { className: "rounded-2xl border border-line p-3", children: [_jsx("summary", { className: "cursor-pointer text-sm font-bold", children: "Profile details" }), _jsxs("div", { className: "mt-3 grid gap-3", children: [isPerformer(kind) ? (_jsxs(_Fragment, { children: [_jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Gender", _jsxs("select", { value: gender, onChange: (e) => setGender(e.target.value), "aria-label": "Gender", className: "h-10 w-full rounded-xl border border-line-2 bg-bg px-3 text-[15px] text-fg", children: [_jsx("option", { value: "", children: "Unspecified" }), _jsx("option", { value: "female", children: "Female" }), _jsx("option", { value: "male", children: "Male" }), _jsx("option", { value: "trans", children: "Trans" })] })] }), _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Profile URL", _jsx("input", { value: profile, onChange: (e) => setProfile(e.target.value), placeholder: "https://\u2026", className: INPUT, inputMode: "url" })] }), _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Biography", _jsx("textarea", { value: bio, onChange: (e) => setBio(e.target.value), placeholder: "Optional biography", rows: 4, className: "w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 py-2.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60" })] })] })) : null, _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: ["Name", _jsx("input", { value: name, onChange: (e) => { setName(e.target.value); setNameMsg(''); }, required: true, className: INPUT }), nameMsg ? _jsx("span", { role: "alert", className: "text-warn", children: nameMsg }) : null] })] })] }), _jsxs("div", { className: "flex flex-wrap items-center justify-end gap-3 border-t border-line pt-3", children: [_jsx("p", { role: "status", "aria-live": "polite", className: "mr-auto text-[11px] text-muted", children: footer }), _jsx("button", { type: "button", disabled: saving, onClick: onDone, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "submit", disabled: saving || !data, className: "h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60", children: saving ? 'Saving…' : saveLabel(kind) })] })] }) }));
}
