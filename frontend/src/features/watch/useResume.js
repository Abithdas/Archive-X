import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { SAVE_EVERY_MS, clearStored, dueForSave, finishedBody, isLastPlayed, labelFor, markPlayed, outcome, progressBody, progressPct, promptPosition, readStored, saveStored, shouldSaveLocal, timeLine } from './resume';
/**
 * Continue-where-you-left-off for the watch page. Before playback it may ask "Resume from 12:05?" (Resume / Start over / Cancel);
 * while playing it saves the position (locally every 5 s, and to the server) and clears it when the video finishes.
 */
export function useResume(o) {
    const { cid, mid } = o;
    const [choice, setChoice] = useState(null);
    const lastLocal = useRef(0), lastServer = useRef(0), lastPos = useRef(0);
    // A new video starts with no choice and no throttling history.
    useEffect(() => { setChoice(null); lastLocal.current = 0; lastServer.current = 0; lastPos.current = 0; }, [cid, mid]);
    const stored = useMemo(() => (o.settled ? promptPosition({ enabled: o.enabled, isLast: isLastPlayed(cid, mid), stored: readStored(cid, mid), duration: o.duration }) : null), [o.settled, o.enabled, o.duration, cid, mid]);
    const asking = stored !== null && choice === null;
    const resumeBtn = useRef(null);
    // The native dialog moves focus to its first control (✕) when it opens; Enter must mean "Resume", so focus it once open.
    useEffect(() => { if (asking)
        resumeBtn.current?.focus(); }, [asking]);
    const ready = o.settled && !asking;
    const decided = choice && stored !== null ? outcome(choice, stored) : null;
    const startAt = decided?.startAt ?? 0;
    const autoPlay = decided ? decided.autoPlay : o.autoplay;
    const onTime = (position, duration) => {
        const now = Date.now();
        if (dueForSave(now, lastLocal.current, SAVE_EVERY_MS)) {
            lastLocal.current = now;
            if (shouldSaveLocal(isLastPlayed(cid, mid), position, duration))
                saveStored(cid, mid, position);
        }
        if (dueForSave(now, lastServer.current, SAVE_EVERY_MS)) {
            lastServer.current = now;
            const body = progressBody(cid, mid, position, duration, lastPos.current);
            lastPos.current = position;
            api('/api/play/progress', { method: 'POST', json: body }).catch(() => { });
        }
    };
    const onPlayed = () => markPlayed(cid, mid);
    const onEnd = () => {
        clearStored(cid, mid);
        api('/api/play/progress', { method: 'POST', json: finishedBody(cid, mid, o.duration) }).catch(() => { });
    };
    const frame = stored !== null ? (o.frameFor?.(stored) ?? null) : null;
    const dialog = asking ? (_jsx(Dialog, { open: true, onClose: () => setChoice('cancel'), title: o.name || 'Continue video', children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-xs font-semibold uppercase tracking-wide text-faint", children: "Archive X \u00B7 Playback" }), _jsxs("p", { id: "resume-position", className: "text-base font-bold", children: ["Resume from ", labelFor(stored), "?"] }), _jsxs("div", { className: "relative aspect-video w-full overflow-hidden rounded-xl bg-surface-2", children: [frame ? _jsx("img", { src: frame, alt: `${o.name} at ${labelFor(stored)}`, className: "size-full object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }) : _jsx("span", { role: "status", className: "absolute inset-0 grid place-items-center text-xs text-muted", children: "Preview unavailable at the saved position" }), _jsx("div", { className: "absolute inset-x-0 bottom-0 h-1 bg-black/40", children: _jsx("span", { className: "block h-full bg-accent", style: { width: `${progressPct(stored, o.duration)}%` } }) }), _jsx("span", { className: "absolute bottom-2 right-2 rounded-md bg-black/65 px-2 py-0.5 text-xs font-semibold text-white", children: timeLine(labelFor(stored), o.duration) })] }), _jsxs("p", { className: "text-sm text-muted", children: ["You stopped at ", labelFor(stored)] }), _jsx("p", { className: "text-[11px] text-faint", children: "Enter resume \u00B7 Esc cancel" }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setChoice('cancel'), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: () => setChoice('start'), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Start over" }), _jsx("button", { ref: resumeBtn, type: "button", onClick: () => setChoice('resume'), className: "h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg", children: "Resume" })] })] }) })) : null;
    return { ready, startAt, autoPlay, dialog, onTime, onPlayed, onEnd };
}
