import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
const why = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : fb);
const providerName = (p) => (p === 'stashdb' ? 'StashDB' : p === 'theporndb' ? 'ThePornDB' : p);
/** Type a performer name: people already in your library first, then performers available through the metadata APIs (picking one downloads it first). */
export function NamePicker({ onPick, onCancel }) {
    const [text, setText] = useState('');
    const [local, setLocal] = useState([]);
    const [remote, setRemote] = useState([]);
    const [searching, setSearching] = useState(false);
    const [configured, setConfigured] = useState(true);
    const [importing, setImporting] = useState('');
    const [err, setErr] = useState('');
    const timer = useRef(undefined);
    const seq = useRef(0);
    useEffect(() => () => clearTimeout(timer.current), []);
    const type = (v) => {
        setText(v);
        setErr('');
        clearTimeout(timer.current);
        const mine = ++seq.current;
        if (v.trim().length < 2) {
            setLocal([]);
            setRemote([]);
            setSearching(false);
            return;
        }
        setSearching(true);
        timer.current = setTimeout(() => {
            const q = encodeURIComponent(v.trim());
            api(`/api/photos/faces/performers?limit=8&q=${q}`).then((r) => { if (mine === seq.current)
                setLocal(r.items ?? []); }).catch(() => { if (mine === seq.current)
                setLocal([]); });
            api(`/api/photos/faces/performers/provider-search?q=${q}`)
                .then((r) => { if (mine === seq.current) {
                setRemote(r.items ?? []);
                setConfigured(r.configured !== false);
            } })
                .catch(() => { if (mine === seq.current)
                setRemote([]); })
                .finally(() => { if (mine === seq.current)
                setSearching(false); });
        }, 250);
    };
    const importOne = async (r) => {
        setImporting(r.provider_id);
        setErr('');
        try {
            const res = await api('/api/photos/faces/performers/import', { method: 'POST', json: { provider: r.provider, provider_id: r.provider_id, name: r.name, image: r.image ?? '', profile: r.profile ?? '' } });
            if (!res?.ok || !res.id)
                throw new ApiError(0, res?.detail || '');
            onPick({ id: res.id, name: res.name ?? r.name });
        }
        catch (e) {
            setErr(why(e, 'Could not add that performer.'));
            setImporting('');
        }
    };
    const row = 'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-fg/10 disabled:opacity-60';
    const none = text.trim().length >= 2 && !searching && !local.length && !remote.length;
    return (_jsxs("div", { className: "grid w-full gap-1", children: [_jsx("input", { autoFocus: true, type: "search", value: text, onChange: (e) => type(e.target.value), onKeyDown: (e) => e.key === 'Escape' && onCancel(), placeholder: "Type a performer name\u2026", "aria-label": "Performer name", className: "h-9 w-full rounded-lg border border-line-2 bg-bg px-3 text-sm outline-none focus:border-accent/60" }), local.length || remote.length || searching || none ? (_jsxs("div", { role: "listbox", "aria-label": "Performers", className: "grid max-h-60 overflow-y-auto rounded-lg border border-line bg-surface p-1 text-left", children: [local.length ? _jsx("div", { className: "px-2 pb-0.5 pt-1 text-[10.5px] font-bold uppercase tracking-wider text-faint", children: "In your library" }) : null, local.map((p) => _jsxs("button", { type: "button", role: "option", "aria-selected": "false", onClick: () => onPick(p), className: row, children: [_jsx("img", { src: p.image_url || `/api/performers/${p.id}/image?w=96`, alt: "", className: "size-7 rounded-full bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), p.name] }, `l${p.id}`)), remote.length ? _jsx("div", { className: "px-2 pb-0.5 pt-2 text-[10.5px] font-bold uppercase tracking-wider text-faint", children: "From metadata providers (added when picked)" }) : null, remote.map((r) => _jsxs("button", { type: "button", role: "option", "aria-selected": "false", disabled: !!importing, onClick: () => void importOne(r), className: row, children: [_jsx("img", { src: r.image || '', alt: "", referrerPolicy: "no-referrer", className: "size-7 rounded-full bg-surface-2 object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), _jsx("span", { className: "min-w-0 flex-1 truncate", children: r.name }), _jsx("small", { className: "text-[11px] text-muted", children: importing === r.provider_id ? 'Adding…' : providerName(r.provider) })] }, `${r.provider}:${r.provider_id}`)), searching ? _jsx("div", { className: "px-2 py-1.5 text-xs text-muted", children: "Searching\u2026" }) : null, none ? _jsx("div", { className: "px-2 py-1.5 text-xs text-muted", children: configured ? 'No performer found.' : 'No performer in your library. Add a StashDB or ThePornDB key in Settings to search online.' }) : null] })) : null, err ? _jsx("p", { role: "alert", className: "text-xs text-bad", children: err }) : null, _jsx("button", { type: "button", onClick: onCancel, className: "w-fit text-xs text-muted hover:underline", children: "Cancel" })] }));
}
