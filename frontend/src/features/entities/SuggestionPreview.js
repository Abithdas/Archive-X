import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/design/Dialog';
import { formatBytes, formatDuration } from '@/features/library/format';
import { FRACTIONS, INCOMPLETE_NOTICE, framePercent, incomplete, liveSrc, previewQuestion, snapshotIndex, snapshotSrc } from './suggestpreview';
import { suggestionTitle, yesLabel } from './studioextras';
function Tile({ s, frac, label, onState }) {
    const cid = String(s.channel_id), mid = Number(s.message_id), idx = snapshotIndex(frac);
    const [src, setSrc] = useState(snapshotSrc(cid, mid, idx));
    const [state, setState] = useState('loading');
    const triedLive = useRef(false);
    const set = (st) => { setState(st); onState(label, st); };
    const live = () => {
        if (triedLive.current)
            return set('failed');
        triedLive.current = true;
        setSrc(liveSrc(cid, mid, Number(s.duration || 0), frac));
    };
    return (_jsxs("div", { className: "relative grid aspect-video place-items-center overflow-hidden rounded-xl bg-black text-center text-xs text-muted", "data-source": state, children: [_jsx("img", { alt: `Verification thumbnail ${label + 1}`, src: src, onLoad: (e) => {
                    if (e.currentTarget.naturalWidth > 1 && e.currentTarget.naturalHeight > 1)
                        set(triedLive.current ? 'live' : 'snapshot');
                    else
                        live();
                }, onError: live, className: state === 'failed' ? 'hidden' : 'absolute inset-0 size-full object-cover' }), state === 'loading' ? _jsx("span", { children: "Loading\u2026" }) : state === 'failed' ? _jsx("span", { children: "Preview unavailable" }) : null, _jsxs("span", { className: "absolute bottom-1.5 right-1.5 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] font-semibold text-white", children: [framePercent(idx), "%"] })] }));
}
/** Four frames of the suggested video, so you can tell whether it really belongs before linking. */
export function SuggestionPreview({ s, name, kind, busy, onConfirm, onReject, onClose }) {
    const states = useRef(FRACTIONS.map(() => 'loading'));
    const warned = useRef(false);
    const report = (i, st) => {
        states.current[i] = st;
        if (!warned.current && incomplete(states.current)) {
            warned.current = true;
            toast(INCOMPLETE_NOTICE);
        }
    };
    useEffect(() => { states.current = FRACTIONS.map(() => 'loading'); warned.current = false; }, [s.suggestion_id]);
    const dur = Number(s.duration || 0);
    return (_jsxs(Dialog, { open: true, onClose: onClose, title: suggestionTitle(s), wide: true, footer: _jsxs("div", { className: "flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3", children: [_jsx("button", { type: "button", disabled: busy, onClick: onReject, className: "h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60", children: "No, don\u2019t link" }), _jsx("button", { type: "button", disabled: busy, onClick: onConfirm, className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg hover:brightness-110 disabled:opacity-60", children: yesLabel(kind) })] }), children: [_jsx("div", { "aria-label": "Video verification thumbnails", className: "grid grid-cols-2 gap-3 sm:grid-cols-4", children: FRACTIONS.map((f, i) => _jsx(Tile, { s: s, frac: f, label: i, onState: report }, f)) }, s.suggestion_id), _jsxs("div", { className: "mt-3 flex flex-wrap gap-2 text-xs font-semibold", children: [_jsx("span", { className: "rounded-md bg-surface-2 px-2 py-1", children: s.filename || 'Telegram video' }), dur ? _jsx("span", { className: "rounded-md bg-surface-2 px-2 py-1", children: formatDuration(dur) }) : null, s.size ? _jsx("span", { className: "rounded-md bg-surface-2 px-2 py-1", children: formatBytes(s.size) }) : null] }), _jsx("p", { className: "mt-2 text-xs text-muted", children: previewQuestion(kind, name) }), busy ? _jsx("p", { role: "status", className: "mt-1 text-xs text-muted", children: "Saving this relationship\u2026" }) : null] }));
}
