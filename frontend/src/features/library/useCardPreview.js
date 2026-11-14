import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import { usePreviewManifest } from '@/api/hooks';
import { loadImage } from '@/features/photos/imageCache';
import { FRAME_COUNT, FRAME_MS, frameSeconds, frameSrc, indexFromPointer, nextIndex, previewReady, ratioFromPointer, shouldCommit, watchedPercent } from './cardpreview';
const hoverCapable = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
/** Hover preview for one video card: the cached snapshots play through, and the pointer scrubs them. Nothing is requested from Telegram. */
export function useCardPreview(cid, mid, duration, thumb) {
    const [hovered, setHovered] = useState(false);
    const [index, setIndex] = useState(0);
    const [frame, setFrame] = useState(null);
    const [ratio, setRatio] = useState(null);
    const auto = useRef(true);
    const seq = useRef(0);
    const lastX = useRef(null);
    const idx = useRef(0);
    idx.current = index;
    const manifest = usePreviewManifest(cid, mid, hovered);
    const ready = hovered && previewReady(manifest.data);
    const history = useQuery({ enabled: hovered, queryKey: ['play-history', cid, mid], staleTime: 10_000, retry: false, queryFn: () => api(`/api/play/history/${encodeURIComponent(cid)}/${mid}`) });
    const revision = String(manifest.data?.revision ?? '');
    const times = manifest.data?.frame_times;
    // Show frame `index` once it has been fetched (a newer request wins), and fetch the next few ahead.
    useEffect(() => {
        if (!ready)
            return;
        const mine = ++seq.current;
        void loadImage(frameSrc(cid, mid, index, revision)).then(() => { if (seq.current === mine)
            setFrame(frameSrc(cid, mid, index, revision)); }).catch(() => undefined);
        for (let n = 1; n < 6; n++)
            void loadImage(frameSrc(cid, mid, (index + n) % FRAME_COUNT, revision)).catch(() => undefined);
    }, [ready, index, cid, mid, revision]);
    // Play on by itself until the pointer starts scrubbing.
    useEffect(() => {
        if (!ready)
            return;
        const t = setInterval(() => { if (auto.current)
            setIndex((i) => nextIndex(i)); }, FRAME_MS);
        return () => clearInterval(t);
    }, [ready]);
    const stop = useCallback(() => {
        seq.current++;
        setHovered(false);
        setFrame(null);
        setRatio(null);
        setIndex(0);
        auto.current = true;
        lastX.current = null;
    }, []);
    useEffect(() => () => { seq.current++; }, []);
    // The card's title link stretches over the whole card, so the pointer events are taken from the card and checked against the picture's area.
    const onPointerLeave = (e) => { if (e.pointerType === 'mouse')
        stop(); };
    const onPointerMove = (e) => {
        if (e.pointerType !== 'mouse' || !hoverCapable())
            return;
        const rect = thumb.current?.getBoundingClientRect();
        if (!rect)
            return;
        const inside = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
        if (!inside) {
            if (hovered)
                stop();
            return;
        }
        if (!hovered) {
            auto.current = true;
            setHovered(true);
            return;
        }
        if (!ready)
            return;
        const candidate = indexFromPointer(e.clientX, rect);
        if (!shouldCommit(lastX.current, e.clientX, idx.current, candidate))
            return;
        lastX.current = e.clientX;
        auto.current = false;
        setIndex(candidate);
        setRatio(ratioFromPointer(e.clientX, rect));
    };
    const previewing = ready && frame !== null;
    return {
        bind: { onPointerLeave, onPointerMove },
        frame: previewing ? frame : null,
        /** shown in place of the duration while previewing */
        seconds: previewing ? frameSeconds(times, index, duration) : null,
        /** 0–1: where the preview is; null when the saved playback position should show instead */
        ratio: previewing ? (ratio ?? index / (FRAME_COUNT - 1)) : null,
        watched: watchedPercent(history.data, duration),
        /** the snapshots are being looked up or fetched */
        loading: hovered && !previewing && (manifest.isPending || (ready && frame === null)),
    };
}
