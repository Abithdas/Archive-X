import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Confirm } from '@/design/Confirm';
import { cn } from '@/design/cn';
import { usePhotoStore } from './store';
export const toggleTag = (picked, tag) => (picked.includes(tag) ? picked.filter((t) => t !== tag) : [...picked, tag]);
export const tagLine = (s) => !s.total_photos ? '' : s.analysed_photos >= s.total_photos ? `All ${s.total_photos.toLocaleString()} photos are tagged.` : `${s.analysed_photos.toLocaleString()} of ${s.total_photos.toLocaleString()} photos are tagged.`;
export const LOOK_CONFIRM = 'A small picture model (CLIP, about 155 MB, downloaded once and checked) runs on this PC. For each photo with a person it looks at the head and suggests hair colour and hair length. These are guesses: hair length is fairly reliable, hair colour is the weakest and is only suggested when the model is clearly sure. It does not identify anyone.';
export const groupLooks = (tags) => tags.reduce((acc, t) => { (acc[t.group] ??= []).push(t); return acc; }, {});
export const TAG_CONFIRM = 'A small detector (about 12 MB) runs on this PC and looks at each photo for visible body-part regions such as breasts, buttocks, belly or feet. It records them as plain tags you can filter on. It does not identify anyone and does not judge size or shape. Photos that are not saved on this PC are fetched from Telegram first.';
const why = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : fb);
export const useContentSummary = () => useQuery({ queryKey: ['content-tags'], queryFn: () => api('/api/photos/content-tags'), staleTime: 15_000 });
/** Filter the photos by what is visible in them; tags come from a small local detector. */
export function ContentTags({ bare = false } = {}) {
    const qc = useQueryClient();
    const { contentTags, setContentTags } = usePhotoStore();
    const q = useContentSummary();
    const [ask, setAsk] = useState(false);
    const [askLooks, setAskLooks] = useState(false);
    const [busy, setBusy] = useState('');
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const s = q.data;
    if (!s || !s.total_photos)
        return null;
    const run = async (kind = 'content') => {
        setAsk(false);
        setAskLooks(false);
        setBusy('Starting…');
        try {
            const r = await api('/api/photos/content-tags/analyze', { method: 'POST', json: kind === 'looks' ? { kind: 'looks' } : {} });
            if (!r?.id)
                throw new ApiError(0, r?.detail || 'Could not start.');
            for (let i = 0; i < 7200 && alive.current; i++) {
                await new Promise((res) => setTimeout(res, 2000));
                let j = null;
                try {
                    j = await api(`/api/photos/scan-status/${encodeURIComponent(r.id)}`);
                }
                catch {
                    continue;
                }
                if (!j || !alive.current)
                    continue;
                const st = String(j.status || '').toUpperCase();
                setBusy(`${j.operation || 'Tagging photos'} · ${Math.round(Number(j.progress || 0))}%`);
                if (['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(st)) {
                    toast(st.startsWith('COMPLETED') ? 'Photos tagged' : `Tagging ${st.toLowerCase()}`);
                    break;
                }
            }
            await Promise.all([qc.invalidateQueries({ queryKey: ['content-tags'] }), qc.invalidateQueries({ queryKey: ['photos'] })]);
        }
        catch (e) {
            toast.error(why(e, 'Could not start tagging.'));
        }
        finally {
            if (alive.current)
                setBusy('');
        }
    };
    return (_jsxs("section", { "aria-label": "Content tags", className: bare ? 'grid gap-2' : 'mb-4 grid gap-2', children: [_jsxs("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1", children: [_jsx("h2", { className: bare ? 'text-sm font-bold' : 'text-lg font-bold', children: "Content" }), _jsx("span", { role: "status", "aria-live": "polite", className: "text-xs text-muted", children: busy || tagLine(s) }), s.available ? _jsx("button", { type: "button", disabled: !!busy, onClick: () => setAsk(true), className: "ml-auto rounded-lg border border-line-2 px-3 py-1 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50", children: s.analysed_photos >= s.total_photos ? 'Tag again' : 'Tag photos by content' }) : _jsxs("span", { className: "ml-auto text-xs text-muted", children: ["Needs ", _jsx("code", { children: "pip install nudenet" })] })] }), s.tags.length ? (_jsxs("div", { role: "group", "aria-label": "Filter by content", className: "flex flex-wrap gap-2", children: [s.tags.map((t) => {
                        const on = contentTags.includes(t.tag);
                        return (_jsxs("button", { type: "button", "aria-pressed": on, onClick: () => setContentTags(toggleTag(contentTags, t.tag)), className: cn('inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm font-semibold transition-colors', on ? 'border-accent bg-accent/15 text-fg' : 'border-line-2 text-muted hover:text-fg'), children: [t.label, _jsx("span", { className: "text-xs tabular-nums text-faint", children: t.count.toLocaleString() })] }, t.tag));
                    }), contentTags.length ? _jsx("button", { type: "button", onClick: () => setContentTags([]), className: "h-8 px-2 text-xs font-semibold text-accent hover:underline", children: "Clear" }) : null] })) : null, s.looks ? (_jsxs("div", { className: "grid gap-2 pt-1", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1", children: [_jsxs("h3", { className: "text-sm font-bold", children: ["Looks ", _jsx("span", { className: "font-medium text-muted", children: "\u00B7 suggested, can be wrong" })] }), _jsx("span", { className: "text-xs text-muted", children: s.looks.total_photos ? `${s.looks.analysed_photos.toLocaleString()} of ${s.looks.total_photos.toLocaleString()} photos looked at.` : '' }), s.looks.available ? _jsx("button", { type: "button", disabled: !!busy, onClick: () => setAskLooks(true), className: "ml-auto rounded-lg border border-line-2 px-3 py-1 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50", children: s.looks.analysed_photos >= s.looks.total_photos ? 'Suggest again' : 'Suggest looks' }) : _jsxs("span", { className: "ml-auto text-xs text-muted", children: ["Needs ", _jsx("code", { children: "pip install onnxruntime tokenizers" })] })] }), Object.entries(groupLooks(s.looks.tags)).map(([group, tags]) => (_jsxs("div", { role: "group", "aria-label": group, className: "flex flex-wrap items-center gap-2", children: [_jsx("span", { className: "w-24 text-xs font-semibold text-faint", children: group }), tags.map((t) => {
                                const on = contentTags.includes(t.tag);
                                return (_jsxs("button", { type: "button", "aria-pressed": on, onClick: () => setContentTags(toggleTag(contentTags, t.tag)), className: cn('inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm font-semibold transition-colors', on ? 'border-accent bg-accent/15 text-fg' : 'border-line-2 text-muted hover:text-fg'), children: [t.label, _jsx("span", { className: "text-xs tabular-nums text-faint", children: t.count.toLocaleString() })] }, t.tag));
                            })] }, group)))] })) : null, ask ? _jsx(Confirm, { title: "Tag photos by content?", text: TAG_CONFIRM, ok: "Tag photos", onCancel: () => setAsk(false), onOk: () => void run('content') }) : null, askLooks ? _jsx(Confirm, { title: "Suggest looks?", text: LOOK_CONFIRM, ok: "Suggest looks", onCancel: () => setAskLooks(false), onOk: () => void run('looks') }) : null] }));
}
