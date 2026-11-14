import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/design/cn';
import { formatDuration } from '@/features/library/format';
import { bucketSecond, choosePreview, keyStep, segmentAt, timeAt } from './timelineLogic';
const clock = (s) => formatDuration(s) || '0:00';
/**
 * Seek bar: buffered + played, chapter segments, a hover/scrub tooltip with the nearest preview frame, the time and the
 * scene title. Dragging scrubs and seeks on release (no flood of range requests while moving); arrow keys, PageUp/Down,
 * Home and End work when focused.
 */
export function Timeline({ time, duration, buffered, segments, preview, onSeek }) {
    const track = useRef(null);
    const [hover, setHover] = useState(null);
    const [drag, setDrag] = useState(null);
    const [imgOk, setImgOk] = useState(true);
    // Live frames, per whole second: 'loading', 'ok' (cached by the browser) or 'failed' (the nearest stored snapshot is then shown, with its own time).
    const [liveState, setLiveState] = useState({});
    const failures = useRef(0);
    const current = useRef(-1);
    const read = (e) => {
        const r = track.current.getBoundingClientRect();
        return { t: timeAt(e.clientX, r.left, r.width, duration), x: Math.max(0, Math.min(r.width, e.clientX - r.left)), w: r.width };
    };
    const shown = drag ?? time;
    const pct = (t) => (duration > 0 ? Math.min(100, (t / duration) * 100) : 0);
    const tip = hover;
    const seg = tip ? segmentAt(segments, tip.t) : undefined;
    const bucket = tip && duration > 0 ? bucketSecond(tip.t, duration) : -1;
    current.current = bucket;
    const liveOk = !!preview?.live && failures.current < 3;
    const choice = tip && duration > 0 ? choosePreview(tip.t, duration, liveState[bucket], preview?.times, liveOk) : null;
    useEffect(() => { setLiveState({}); failures.current = 0; }, [preview]);
    // Ask for the exact frame once the pointer has rested on a second for a moment (a little longer while dragging).
    useEffect(() => {
        if (!liveOk || bucket < 0 || !preview?.live)
            return;
        if (liveState[bucket] === 'ok' || liveState[bucket] === 'failed' || liveState[bucket] === 'loading')
            return;
        const t = setTimeout(() => {
            setLiveState((m) => ({ ...m, [bucket]: 'loading' }));
            const img = new Image();
            img.onload = () => { failures.current = 0; setLiveState((m) => ({ ...m, [bucket]: 'ok' })); };
            img.onerror = () => {
                // a frame the server dropped because the pointer had moved on is simply forgotten; one that fails where the pointer still is counts
                if (current.current === bucket) {
                    failures.current++;
                    setLiveState((m) => ({ ...m, [bucket]: 'failed' }));
                }
                else
                    setLiveState((m) => { const n = { ...m }; delete n[bucket]; return n; });
            };
            img.src = preview.live(bucket);
        }, drag !== null ? 140 : 90);
        return () => clearTimeout(t);
    }, [bucket, liveOk, liveState, drag, preview]);
    /** A click or release seeks to the moment of the picture that was showing, so the video starts exactly there. */
    const target = (t) => {
        const c = duration > 0 ? choosePreview(t, duration, liveState[bucketSecond(t, duration)], preview?.times, liveOk) : null;
        return c && c.kind !== 'none' ? c.time : t;
    };
    return (_jsxs("div", { ref: track, role: "slider", tabIndex: duration ? 0 : -1, "aria-label": "Seek", "aria-valuemin": 0, "aria-valuemax": Math.floor(duration), "aria-valuenow": Math.floor(shown), "aria-valuetext": `${clock(shown)} of ${duration ? clock(duration) : 'unknown'}`, "aria-disabled": !duration, onKeyDown: (e) => {
            const n = keyStep(e.key, time, duration);
            if (n !== null && duration) {
                e.preventDefault();
                e.stopPropagation();
                onSeek(n);
            }
        }, onPointerDown: (e) => {
            if (!duration)
                return;
            e.currentTarget.setPointerCapture(e.pointerId);
            const p = read(e);
            setDrag(p.t);
            setHover({ t: p.t, x: p.x });
        }, onPointerMove: (e) => {
            if (!duration)
                return;
            const p = read(e);
            setHover({ t: p.t, x: p.x });
            if (drag !== null)
                setDrag(p.t);
        }, onPointerUp: (e) => {
            if (drag === null)
                return;
            const p = read(e);
            setDrag(null);
            onSeek(target(p.t));
            if (e.pointerType !== 'mouse')
                setHover(null);
        }, onPointerCancel: () => { setDrag(null); setHover(null); }, onPointerLeave: () => drag === null && setHover(null), className: "group relative h-7 w-full cursor-pointer touch-none select-none rounded outline-none focus-visible:ring-2 focus-visible:ring-accent/60", children: [tip && duration && choice ? (_jsxs("div", { className: "pointer-events-none absolute bottom-full z-20 mb-2 grid w-max max-w-[220px] -translate-x-1/2 justify-items-center gap-1", style: { left: `clamp(${preview ? 82 : 28}px, ${tip.x}px, calc(100% - ${preview ? 82 : 28}px))` }, children: [choice.kind === 'live' ? (_jsx("img", { src: preview.live(choice.time), alt: "", width: 160, height: 90, className: "h-[90px] w-[160px] rounded-lg border border-line-2 bg-black object-cover shadow-[0_8px_24px_rgb(0_0_0/0.5)]" }, `live-${choice.time}`)) : choice.kind === 'loading' ? (_jsx("span", { role: "status", "aria-label": "Loading preview", className: "grid h-[90px] w-[160px] place-items-center rounded-lg border border-line-2 bg-black/80 shadow-[0_8px_24px_rgb(0_0_0/0.5)]", children: _jsx("span", { className: "size-6 animate-spin rounded-full border-2 border-white/20 border-t-white" }) })) : choice.kind === 'snapshot' && imgOk ? (_jsx("img", { src: preview.url(choice.frame), alt: "", width: 160, height: 90, onError: () => setImgOk(false), className: "h-[90px] w-[160px] rounded-lg border border-line-2 bg-black object-cover shadow-[0_8px_24px_rgb(0_0_0/0.5)]" })) : null, seg ? _jsx("span", { className: "max-w-[200px] truncate rounded-md bg-black/75 px-2 py-0.5 text-[11px] font-semibold text-white", children: seg.title }) : null, _jsx("span", { className: "rounded-md bg-black/80 px-2 py-0.5 text-xs font-bold tabular-nums text-white", children: clock(choice.time) })] })) : null, _jsxs("div", { className: cn('absolute inset-x-0 top-1/2 -translate-y-1/2 transition-[height] duration-150', tip || drag !== null ? 'h-2' : 'h-1'), children: [_jsxs("div", { className: "absolute inset-0 overflow-hidden rounded-full bg-fg/20", children: [_jsx("span", { className: "absolute inset-y-0 left-0 bg-fg/20", style: { width: `${pct(buffered)}%` } }), _jsx("span", { className: "absolute inset-y-0 left-0 bg-fg", style: { width: `${pct(shown)}%` } })] }), segments.map((s, i) => (_jsx("span", { "aria-hidden": "true", "data-segment": s.title, className: "absolute inset-y-0 border-r-2 border-bg last:border-r-0", style: { left: `${pct(s.start)}%`, width: `${pct(s.end) - pct(s.start)}%` } }, i))), _jsx("span", { "aria-hidden": "true", className: cn('absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg shadow-md transition-transform', tip || drag !== null ? 'scale-100' : 'scale-0 group-focus-visible:scale-100'), style: { left: `${pct(shown)}%` } })] })] }));
}
