import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/design/cn';
import { firstLoaded, loadImage } from './imageCache';
import { IconClose, IconFit, IconFullscreen, IconNext, IconPause, IconPlay, IconPrev, IconRotateLeft, IconRotateRight, IconStar, IconZoomIn, IconZoomOut } from './ViewerIcons';
import { MIN_REL, SLIDE_MS, SWIPE_PX, anchorPan, paddedBox, clampPan, clampScale, pinchScale, wheelFactor, wheelSteps, counterText, favKey, fitScale, hud, neighborUrls, photoName, rotate, stepIndex, stripRange, zoomBy, zoomPercent } from './photos';
const PILL = 'grid size-10 shrink-0 place-items-center rounded-full text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-35 disabled:hover:bg-transparent aria-pressed:bg-white/20 sm:size-9';
const NOBAR = '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden';
const DIVIDER = 'mx-1 hidden h-5 w-px bg-white/25 sm:block';
/** Full-screen photo viewer: preview first, original swapped in when ready; zoom, pan, rotate, slideshow, filmstrip, keyboard. */
export function PhotoViewer({ items, index, total, favorites, onIndex, onClose, onFavorite, onNeedMore }) {
    const ref = useRef(null);
    const stage = useRef(null);
    const strip = useRef(null);
    const item = items[index];
    const [box, setBox] = useState({ w: 0, h: 0 });
    const [nat, setNat] = useState({ w: 0, h: 0 });
    const [mul, setMul] = useState(1); // magnification relative to the fitted size
    const [rot, setRot] = useState(0);
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const [playing, setPlaying] = useState(false);
    const [fs, setFs] = useState(false);
    // What is on screen. It changes only once the next photo is fully decoded, so moving between photos never shows a blank or a blurry stand-in.
    const [shown, setShown] = useState(null);
    const [slow, setSlow] = useState(false);
    const [anim, setAnim] = useState(false); // ease zoom steps and fit; never while dragging or when a new photo appears
    const pointers = useRef(new Map());
    const pinch = useRef(null);
    const toolbar = useRef(null);
    const drag = useRef(null);
    useEffect(() => {
        const d = ref.current;
        if (d && !d.open)
            d.showModal();
    }, []);
    useEffect(() => {
        const el = stage.current;
        if (!el)
            return;
        const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
        measure();
        if (typeof ResizeObserver !== 'function')
            return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    const key = item ? `${item.channel_id}:${item.message_id}` : '';
    // Load the photo (the preview and the original race; the original takes over if it was second), then swap it in with a fresh fit.
    useEffect(() => {
        if (!item)
            return;
        let live = true;
        setSlow(false);
        const t = setTimeout(() => { if (live)
            setSlow(true); }, 900);
        const urls = [item.image_url, item.preview_url].filter(Boolean);
        void firstLoaded(urls).then(({ url, img }) => {
            if (!live)
                return;
            clearTimeout(t);
            setSlow(false);
            setAnim(false);
            setMul(1);
            setRot(0);
            setPan({ x: 0, y: 0 });
            setNat({ w: item.width || img.naturalWidth, h: item.height || img.naturalHeight });
            setShown({ key, src: url, alt: photoName(item) });
            if (url !== item.image_url && item.image_url)
                void loadImage(item.image_url).then(() => { if (live)
                    setShown({ key, src: item.image_url, alt: photoName(item) }); }).catch(() => undefined);
        }).catch(() => { if (live) {
            clearTimeout(t);
            setSlow(false);
        } });
        return () => { live = false; clearTimeout(t); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key]);
    useEffect(() => { if (items.length - index <= 6 && items.length < total)
        onNeedMore(); }, [index, items.length, total, onNeedMore]);
    // Fetch the neighbours at full quality now (next two, previous one), so Next and Previous are instant.
    useEffect(() => {
        for (const u of neighborUrls(items, index))
            void loadImage(u).catch(() => undefined);
    }, [items, index]);
    const fit = fitScale(nat, paddedBox(box), rot); // leave some air above and below the photo
    const scale = fit * mul;
    const atFit = Math.abs(mul - 1) < 0.001;
    const go = useCallback((d, wrap = false) => { const t = stepIndex(index, d, items.length, wrap); if (t !== null)
        onIndex(t); }, [index, items.length, onIndex]);
    const setScale = (s) => { const m = Math.max(MIN_REL, s / fit); setAnim(true); setMul(m); setPan((p) => clampPan(p.x, p.y, nat, box, fit * m, rot)); };
    const zoomStep = (dir) => setScale(zoomBy(scale, dir, fit));
    /** Zoom to `to`, keeping the point under the pointer where it is. */
    const zoomAt = (to, client, animate = true) => {
        const r = stage.current?.getBoundingClientRect();
        if (!r)
            return;
        const z = r.width / (stage.current.clientWidth || r.width || 1) || 1; // the page is zoomed on large screens; pan and scale are in unzoomed pixels
        const cursor = { x: (client.x - (r.left + r.width / 2)) / z, y: (client.y - (r.top + r.height / 2)) / z };
        const next = clampScale(to, fit);
        const m = Math.max(MIN_REL, next / fit);
        setAnim(animate);
        setMul(m);
        const p = anchorPan(pan, scale, fit * m, cursor);
        setPan(clampPan(p.x, p.y, nat, box, fit * m, rot));
    };
    const doFit = () => { setAnim(true); setMul(1); setPan({ x: 0, y: 0 }); };
    const turn = (deg) => { setRot((r) => rotate(r, deg)); setMul(1); setPan({ x: 0, y: 0 }); };
    const toggleFs = useCallback(() => {
        const d = ref.current;
        if (!d)
            return;
        if (document.fullscreenElement)
            void document.exitFullscreen?.();
        else
            void d.requestFullscreen?.().catch(() => undefined);
    }, []);
    useEffect(() => {
        const on = () => setFs(Boolean(document.fullscreenElement));
        document.addEventListener('fullscreenchange', on);
        return () => document.removeEventListener('fullscreenchange', on);
    }, []);
    useEffect(() => {
        if (!playing)
            return;
        const t = setInterval(() => go(1, true), SLIDE_MS);
        return () => clearInterval(t);
    }, [playing, go]);
    useEffect(() => {
        const onKey = (e) => {
            if (e.target?.closest?.('input,textarea,select,[contenteditable="true"]'))
                return;
            const k = e.key;
            const act = k === 'ArrowLeft' ? () => go(-1) : k === 'ArrowRight' ? () => go(1) : k === 'ArrowUp' || k === '+' || k === '=' ? () => zoomStep(1)
                : k === 'ArrowDown' || k === '-' || k === '_' ? () => zoomStep(-1) : k === '0' ? doFit : k === 'r' || k === 'R' ? () => turn(e.shiftKey ? -90 : 90)
                    : k === 'f' || k === 'F' ? toggleFs : k === ' ' ? () => setPlaying((p) => !p) : null;
            if (act) {
                e.preventDefault();
                act();
            }
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    });
    useEffect(() => {
        const cur = strip.current?.querySelector('[aria-current="true"]');
        cur?.scrollIntoView?.({ block: 'nearest', inline: 'center' });
    }, [index]);
    const down = (e) => {
        pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
        e.currentTarget.setPointerCapture?.(e.pointerId);
        if (pointers.current.size === 2) {
            const [a, b] = [...pointers.current.values()];
            pinch.current = { dist: Math.hypot(b.x - a.x, b.y - a.y), scale, pan };
            drag.current = null;
            return;
        }
        drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y, moved: false };
    };
    const move = (e) => {
        if (pointers.current.has(e.pointerId))
            pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const pc = pinch.current;
        if (pc && pointers.current.size === 2) {
            const [a, b] = [...pointers.current.values()];
            const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
            zoomAt(pinchScale(pc.scale, pc.dist, Math.hypot(b.x - a.x, b.y - a.y)), mid, false);
            return;
        }
        const d = drag.current;
        if (!d || atFit)
            return;
        if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3)
            d.moved = true;
        setAnim(false);
        // pointer distances are screen pixels; the photo moves in the page's (possibly zoomed) pixels
        const r = stage.current?.getBoundingClientRect();
        const z = r && stage.current.clientWidth ? r.width / stage.current.clientWidth : 1;
        setPan(clampPan(d.px + (e.clientX - d.x) / z, d.py + (e.clientY - d.y) / z, nat, box, scale, rot));
    };
    const up = (e) => {
        pointers.current.delete(e.pointerId);
        if (pinch.current) {
            if (pointers.current.size < 2)
                pinch.current = null;
            drag.current = null;
            return;
        }
        const d = drag.current;
        drag.current = null;
        if (d && atFit) {
            const dx = e.clientX - d.x;
            if (Math.abs(dx) >= SWIPE_PX)
                go(dx < 0 ? 1 : -1);
        }
    };
    // Wheel over the photo zooms toward the pointer; over the toolbar or the filmstrip it turns the pages. Native listeners, because a wheel handler must be able to stop the page scrolling.
    const wheelPhoto = useRef(() => undefined);
    wheelPhoto.current = (e) => {
        if (e.target.closest('button'))
            return;
        e.preventDefault();
        const r = stage.current?.getBoundingClientRect();
        zoomAt(scale * wheelFactor(e.deltaY, e.deltaMode, r?.height ?? 800), { x: e.clientX, y: e.clientY });
    };
    const wheelPages = useRef(() => undefined);
    wheelPages.current = (e) => {
        if (e.ctrlKey || e.metaKey)
            return;
        const n = wheelSteps(e.deltaX, e.deltaY);
        if (!n)
            return;
        e.preventDefault();
        const dir = n > 0 ? 1 : -1;
        for (let i = 0; i < Math.abs(n); i++)
            go(dir);
    };
    useEffect(() => {
        const els = [
            [stage.current, (e) => wheelPhoto.current(e)], [toolbar.current, (e) => wheelPages.current(e)], [strip.current, (e) => wheelPages.current(e)],
        ];
        for (const [el, fn] of els)
            el?.addEventListener('wheel', fn, { passive: false });
        return () => { for (const [el, fn] of els)
            el?.removeEventListener('wheel', fn); };
    });
    const h = useMemo(() => (item ? hud(item) : null), [item]);
    const [from, to] = stripRange(index, items.length);
    const fav = item ? favorites.has(favKey(item)) : false;
    if (!item || !h)
        return null;
    return createPortal(_jsxs("dialog", { ref: ref, "aria-label": "Photo viewer", onClose: onClose, onCancel: (e) => { e.preventDefault(); onClose(); }, onClick: (e) => e.target === ref.current && onClose(), className: "fixed inset-0 m-0 hidden h-[calc(100dvh/var(--ax-page-zoom,1))] max-h-none w-[calc(100vw/var(--ax-page-zoom,1))] max-w-none flex-col bg-black/95 p-0 text-white open:flex", children: [_jsxs("div", { className: "grid shrink-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5 px-3 pb-1 pt-[max(8px,env(safe-area-inset-top))] sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:gap-x-3 sm:py-2", children: [_jsx("b", { className: "min-w-0 truncate text-sm sm:col-start-1 sm:row-start-1 sm:max-w-[34vw]", title: h.title, children: h.title }), _jsxs("div", { className: cn('col-span-2 row-start-2 flex min-w-0 items-center gap-1.5 overflow-x-auto text-xs sm:col-span-1 sm:col-start-2 sm:row-start-1', NOBAR), children: [[h.dims, h.mp, h.size, h.date].filter(Boolean).map((t) => _jsx("span", { className: "shrink-0 whitespace-nowrap rounded-full bg-white/10 px-2 py-0.5", children: t }, t)), h.channel ? _jsx("span", { className: "max-w-[60vw] shrink-0 truncate rounded-full bg-white/10 px-2 py-0.5", title: h.channel, children: h.channel }) : null] }), _jsxs("div", { className: "flex items-center gap-1 sm:col-start-3 sm:row-start-1", children: [_jsx("span", { "aria-live": "polite", className: "rounded-full bg-white/10 px-2.5 py-1 text-xs font-semibold tabular-nums", children: counterText(index, total || items.length) }), _jsx("button", { type: "button", onClick: onClose, "aria-label": "Close photo viewer", title: "Close (Esc)", className: PILL, children: _jsx(IconClose, {}) })] })] }), _jsxs("div", { ref: stage, role: "img", "aria-label": `${h.title}. Scroll to zoom, drag to pan, scroll over the toolbar or use arrow keys to change photo.`, onPointerDown: down, onPointerMove: move, onPointerUp: up, onPointerCancel: (e) => { pointers.current.delete(e.pointerId); pinch.current = null; drag.current = null; }, onDoubleClick: (e) => (atFit ? zoomAt(fit * 2.5, { x: e.clientX, y: e.clientY }) : doFit()), className: cn('relative min-h-0 flex-1 touch-none select-none overflow-hidden', atFit ? 'cursor-zoom-in' : 'cursor-grab active:cursor-grabbing'), children: [shown ? _jsx("img", { src: shown.src, alt: shown.alt, draggable: false, style: { width: nat.w || undefined, height: nat.h || undefined, maxWidth: 'none', transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) rotate(${rot}deg) scale(${scale})` }, className: cn('absolute left-1/2 top-1/2 origin-center', anim && 'transition-transform duration-150 ease-out') }) : null, slow ? _jsx("div", { role: "status", "aria-label": "Loading selected photo", className: "absolute inset-0 grid place-items-center", children: _jsx("span", { className: "size-10 animate-spin rounded-full border-4 border-white/20 border-t-white" }) }) : null] }), _jsxs("div", { className: "shrink-0 px-3 pb-[max(12px,env(safe-area-inset-bottom))]", children: [_jsxs("div", { ref: toolbar, role: "toolbar", "aria-label": "Photo viewer controls", className: "mx-auto mb-2.5 flex w-full max-w-[420px] flex-col gap-0.5 rounded-3xl bg-white/10 p-1.5 backdrop-blur sm:w-fit sm:max-w-full sm:flex-row sm:items-center sm:gap-0.5 sm:rounded-full sm:px-2 sm:py-1", children: [_jsxs("div", { className: "flex items-center justify-center gap-1 sm:gap-0.5", children: [_jsx("button", { type: "button", className: PILL, onClick: () => go(-1), disabled: index <= 0, title: "Previous photo (\u2190)", "aria-label": "Previous photo", children: _jsx(IconPrev, {}) }), _jsx("button", { type: "button", className: PILL, onClick: () => setPlaying(!playing), "aria-pressed": playing, title: playing ? 'Pause slideshow (Space)' : 'Play slideshow (Space)', "aria-label": playing ? 'Pause slideshow' : 'Play slideshow', children: playing ? _jsx(IconPause, {}) : _jsx(IconPlay, {}) }), _jsx("button", { type: "button", className: PILL, onClick: () => go(1), disabled: index >= items.length - 1, title: "Next photo (\u2192)", "aria-label": "Next photo", children: _jsx(IconNext, {}) })] }), _jsx("span", { "aria-hidden": "true", className: DIVIDER }), _jsxs("div", { className: "flex items-center justify-between sm:justify-center sm:gap-0.5", children: [_jsx("button", { type: "button", className: PILL, onClick: () => zoomStep(-1), title: "Zoom out (\u2193)", "aria-label": "Zoom out", children: _jsx(IconZoomOut, {}) }), _jsx("span", { role: "status", "aria-live": "polite", "aria-label": "Zoom level", className: "min-w-12 text-center text-xs font-semibold tabular-nums", children: zoomPercent(scale, fit) }), _jsx("button", { type: "button", className: PILL, onClick: () => zoomStep(1), title: "Zoom in (\u2191)", "aria-label": "Zoom in", children: _jsx(IconZoomIn, {}) }), _jsx("button", { type: "button", className: PILL, onClick: doFit, title: "Fit full image (0)", "aria-label": "Fit image", children: _jsx(IconFit, {}) }), _jsx("span", { "aria-hidden": "true", className: DIVIDER }), _jsx("button", { type: "button", className: PILL, onClick: () => turn(-90), title: "Rotate counter-clockwise (Shift+R)", "aria-label": "Rotate counter-clockwise", children: _jsx(IconRotateLeft, {}) }), _jsx("button", { type: "button", className: PILL, onClick: () => turn(90), title: "Rotate clockwise (R)", "aria-label": "Rotate clockwise", children: _jsx(IconRotateRight, {}) }), _jsx("button", { type: "button", className: cn(PILL, fav && 'text-warn'), onClick: () => onFavorite(item), "aria-pressed": fav, title: fav ? 'Remove from favourites' : 'Add to favourites', "aria-label": fav ? 'Remove from favourites' : 'Add to favourites', children: _jsx(IconStar, { on: fav }) }), _jsx("button", { type: "button", className: PILL, onClick: toggleFs, "aria-pressed": fs, title: "Toggle fullscreen (F)", "aria-label": "Toggle fullscreen", children: _jsx(IconFullscreen, { on: fs }) })] })] }), _jsx("div", { ref: strip, "aria-label": "Photo carousel", className: cn('flex gap-1.5 overflow-x-auto', NOBAR), children: items.slice(from, to).map((p, k) => {
                            const i = from + k;
                            return (_jsxs("button", { type: "button", onClick: () => onIndex(i), "aria-label": `Photo ${i + 1}: ${photoName(p)}`, "aria-current": i === index ? 'true' : undefined, className: cn('relative h-12 w-[68px] shrink-0 overflow-hidden rounded-lg border-2 opacity-70 hover:opacity-100 sm:h-14 sm:w-20', i === index ? 'border-accent opacity-100' : 'border-transparent'), children: [_jsx("img", { src: p.thumbnail_url, alt: "", loading: "lazy", draggable: false, className: "size-full object-cover" }), favorites.has(favKey(p)) ? _jsx("span", { "aria-hidden": "true", className: "absolute right-0.5 top-0 text-xs text-warn", children: "\u2605" }) : null] }, `${p.channel_id}:${p.message_id}`));
                        }) })] })] }), document.body);
}
