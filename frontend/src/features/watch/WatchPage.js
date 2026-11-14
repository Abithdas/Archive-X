import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Link, useNavigate, useRouter } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, thumbUrl } from '@/api/client';
import { useEntityLookup, useFavorite, useLibrary, useMarkers, usePlayInfo, usePreviewManifest, useVideo } from '@/api/hooks';
import { Icon } from '@/design/Icon';
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { formatBytes } from '@/design/format';
import { openExternal } from '@/features/library/CardMenu';
import { formatDuration } from '@/features/library/format';
import { useLibraryStore } from '@/features/library/store';
import { experienceFrom } from '@/features/settings/logic';
import { useExperienceSettings } from '@/features/settings/queries';
import { gridReady, gridTimes } from './grid';
import { MarkersSection } from './MarkersSection';
import { QuickEditDialog } from './QuickEditDialog';
import { Player } from './Player';
import { useResume } from './useResume';
import { frameIndexFor, normaliseChapters, toSegments } from './timelineLogic';
import { withWidth } from '@/design/responsive';
import { AnimatePresence, listItem, motion } from '@/design/motion';
const CARD = 'rounded-[18px] border border-line bg-surface p-4';
const CHIP = 'inline-flex h-7 items-center rounded-full bg-surface-3 px-3 text-xs font-semibold text-muted';
/** Telegram deep link for a message in a channel (-100… ids are public-link channel ids). */
export const telegramLink = (cid, mid) => (/^-100\d+$/.test(cid) ? `https://t.me/c/${cid.slice(4)}/${mid}` : null);
function Skel() {
    return (_jsxs(SkeletonGroup, { label: "Loading video", className: "mx-auto grid w-full max-w-[1500px] gap-4 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_340px]", children: [_jsxs("div", { className: "grid gap-4 text-sm leading-snug", children: [_jsx(Skeleton, { className: "aspect-video w-full rounded-[18px]" }), _jsx("div", { className: "text-3xl", children: _jsx(SkeletonLine, { width: "55%" }) }), _jsx(SkeletonLine, { width: "30%" }), _jsx(Skeleton, { className: "h-40 w-full rounded-[18px]" })] }), _jsx(Skeleton, { className: "hidden h-96 rounded-[18px] lg:block" })] }));
}
function UpNext({ items, current }) {
    const rest = items.filter((i) => !(i.id === current.id && i._cid === current._cid)).slice(0, 10);
    return (_jsxs("section", { "aria-label": "Up next videos", className: cn(CARD, 'self-start'), children: [_jsxs("div", { className: "mb-3 flex items-baseline justify-between", children: [_jsx("h2", { className: "text-lg font-extrabold", children: "Up next" }), _jsx("span", { className: "text-xs text-faint", children: "Current queue" })] }), _jsxs("ul", { className: "grid gap-2.5", children: [_jsx(AnimatePresence, { initial: false, children: rest.map((i) => (_jsx(motion.li, { ...listItem, children: _jsxs(Link, { to: "/watch/$cid/$mid", params: { cid: i._cid, mid: String(i.id) }, className: "flex gap-3 rounded-xl p-1 hover:bg-fg/5", children: [_jsxs("span", { className: "relative aspect-video w-[124px] shrink-0 overflow-hidden rounded-lg bg-surface-2", children: [_jsx("img", { src: withWidth(thumbUrl(i._cid, i.thumbnail_id), 320), alt: "", loading: "lazy", className: "h-full w-full object-cover", onError: (e) => (e.currentTarget.style.visibility = 'hidden') }), i.duration ? _jsx("span", { className: "absolute bottom-1 right-1 rounded-md bg-black/65 px-1.5 text-[11px] font-semibold text-white", children: formatDuration(i.duration) }) : null] }), _jsx("span", { className: "line-clamp-3 min-w-0 self-center text-[13px] font-semibold leading-snug [overflow-wrap:anywhere]", children: i.name })] }) }, `${i._cid}:${i.id}`))) }), !rest.length ? _jsx("li", { className: "text-sm text-muted", children: "Nothing else in this queue." }) : null] })] }));
}
export function WatchPage({ cid, mid }) {
    const video = useVideo(cid, mid);
    const router = useRouter();
    const navigate = useNavigate();
    const { channels, sort } = useLibraryStore();
    const queue = useLibrary({ channels: channels.length ? channels : [cid], page: 0, per: 24, sort });
    const markers = useMarkers(video.data?.media_id);
    const manifest = usePreviewManifest(cid, mid);
    const playInfo = usePlayInfo(cid, mid);
    // Playback waits for the codec facts, but never longer than 2.5 s (a slow Telegram must not stall the player).
    const [infoCapped, setInfoCapped] = useState(false);
    useEffect(() => { setInfoCapped(false); const t = setTimeout(() => setInfoCapped(true), 2500); return () => clearTimeout(t); }, [cid, mid]);
    const infoWait = playInfo.isPending && !infoCapped;
    const fav = useFavorite();
    const expQuery = useExperienceSettings();
    const exp = experienceFrom(expQuery.data);
    const [showTags, setShowTags] = useState(false);
    const [editing, setEditing] = useState(false);
    const [seekRequest, setSeekRequest] = useState();
    const item = video.data;
    const performers = useEntityLookup('performers', item?.performers ?? []);
    const studios = useEntityLookup('studios', [item?.studio, ...(item?.studios ?? [])]);
    const items = queue.data?.items ?? [];
    const index = item ? items.findIndex((i) => i.id === item.id && i._cid === item._cid) : -1;
    const go = (i) => i && void navigate({ to: '/watch/$cid/$mid', params: { cid: i._cid, mid: String(i.id) } });
    const prev = index > 0 ? items[index - 1] : undefined;
    const next = index >= 0 ? items[index + 1] : items.find((i) => item && !(i.id === item.id && i._cid === item._cid));
    const segments = useMemo(() => toSegments(normaliseChapters(markers.data, item?.duration ?? 0), item?.duration ?? 0), [markers.data, item?.duration]);
    // Live seek preview: a frame made at the exact second under the pointer; the stored snapshots (when all cached) are the fallback.
    const snapshotsReady = Boolean(manifest.data?.frames_ready && manifest.data.frame_times.length);
    const preview = exp.live_seek_preview ? { times: snapshotsReady ? manifest.data.frame_times : [], url: (n) => `/api/play/preview-frame/${encodeURIComponent(cid)}/${mid}/${n}`, live: (t) => `/api/play/preview/${encodeURIComponent(cid)}/${mid}?t=${t.toFixed(3)}&w=320&pv=15&lw=1` } : undefined;
    const frameFor = manifest.data?.frames_ready && manifest.data.frame_times.length ? (t) => `/api/play/preview-frame/${encodeURIComponent(cid)}/${mid}/${frameIndexFor(manifest.data.frame_times, t)}` : undefined;
    const snapshots = useMemo(() => {
        const m = manifest.data;
        if (!gridReady(m))
            return undefined;
        return {
            times: gridTimes(m, item?.duration ?? 0),
            spriteUrl: m.sprite_ready ? `/api/play/preview-grid/${encodeURIComponent(cid)}/${mid}` : null,
            frameUrl: (i) => `/api/play/preview-frame/${encodeURIComponent(cid)}/${mid}/${i}`,
            aspect: Number(m.frame_width || 320) / Math.max(1, Number(m.frame_height || 180)),
        };
    }, [manifest.data, item?.duration, cid, mid]);
    const resume = useResume({ cid, mid, name: item?.name ?? '', duration: item?.duration ?? 0, enabled: exp.resume_prompt, settled: Boolean(item) && !expQuery.isPending, autoplay: exp.autoplay, frameFor });
    const idOf = (list, name) => list?.find((e) => e.name.toLowerCase() === name.toLowerCase())?.id;
    if (video.isPending)
        return _jsx(Skel, {});
    if (video.isError || !item) {
        const missing = video.error instanceof ApiError && video.error.status === 404;
        return (_jsxs("section", { role: "alert", className: "mx-auto grid max-w-xl place-items-center gap-3 p-10 text-center", children: [_jsx("p", { className: "font-semibold", children: missing ? 'Video not found.' : 'Could not load this video.' }), missing ? null : _jsx("button", { type: "button", onClick: () => void video.refetch(), className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg", children: "Retry" }), _jsx(Link, { to: "/videos", className: "font-semibold text-accent underline", children: "Back to videos" })] }));
    }
    const tg = telegramLink(item._cid, item.id);
    const studio = item.studio || item.studios[0] || '';
    const vlc = () => void openExternal(item, 'vlc').then((r) => toast(`Opened in ${r.player ?? 'VLC'}`)).catch((e) => toast.error(`Player error: ${e instanceof ApiError ? e.detail : 'not found'}`));
    const mpc = () => void openExternal(item, 'mpc').then((r) => toast(`Opened in ${r.player ?? 'MPC-HC'}`)).catch((e) => toast.error(`Player error: ${e instanceof ApiError ? e.detail : 'not found'}`));
    const tags = showTags ? item.tags : item.tags.slice(0, 12);
    return (_jsxs("div", { className: "mx-auto grid w-full max-w-[1500px] gap-5 px-4 pb-10 pt-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_340px]", children: [_jsxs("div", { className: "grid min-w-0 content-start gap-4", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("button", { type: "button", onClick: () => router.history.back(), "aria-label": "Go back", title: "Go back", className: "grid size-10 place-items-center rounded-xl border border-line bg-surface hover:bg-surface-2", children: _jsx(Icon, { name: "arrow-left" }) }), tg ? _jsxs("a", { href: tg, target: "_blank", rel: "noreferrer", className: "inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold hover:bg-surface-2", children: [_jsx(Icon, { name: "brand-telegram" }), "Telegram"] }) : null] }), resume.ready ? (_jsx(Player, { media: { cid: String(item._cid), mid: item.id }, info: playInfo.data, infoPending: infoWait, src: `/stream/${encodeURIComponent(item._cid)}/${item.id}`, durationHint: item.duration, segments: segments, preview: preview, autoPlay: resume.autoPlay, startAt: resume.startAt, snapshots: snapshots, seekRequest: seekRequest, onTime: resume.onTime, onPlayed: resume.onPlayed, onEnd: resume.onEnd, autoNext: exp.autonext, onPrev: prev ? () => go(prev) : undefined, onNext: next ? () => go(next) : undefined, onOpenVlc: vlc })) : (_jsx("div", { className: "aspect-video w-full rounded-[18px] border border-line bg-black", "aria-hidden": "true" })), resume.dialog, editing ? _jsx(QuickEditDialog, { item: item, onDone: () => setEditing(false) }, `${item.id}:${item._cid}`) : null, _jsxs("header", { className: "min-w-0", children: [_jsx("h1", { className: "text-[clamp(22px,2.4vw,30px)] font-extrabold leading-tight tracking-tight [overflow-wrap:anywhere]", children: item.name }), _jsxs("p", { className: "mt-1 truncate text-xs text-faint", children: ["Telegram filename: ", item.filename] }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-2", children: [item.resolution_category ? _jsx("span", { className: cn(CHIP, 'bg-good/15 text-good'), children: item.resolution_category }) : null, item.duration ? _jsx("span", { className: CHIP, children: formatDuration(item.duration) }) : null, item.size ? _jsx("span", { className: CHIP, children: formatBytes(item.size) }) : null, item.codec ? _jsx("span", { className: CHIP, children: item.codec }) : null, studio ? _jsx("span", { className: cn(CHIP, 'gap-2'), children: _jsx("b", { className: "text-fg", children: studio }) }) : null] })] }), _jsxs("section", { className: CARD, "aria-label": "Performers and studio", children: [_jsxs("div", { className: "mb-3 flex items-start justify-between gap-3", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-base font-bold", children: "Performers & Studio" }), _jsx("p", { className: "text-xs text-faint", children: "People and studio linked to this video" })] }), _jsx("button", { type: "button", onClick: () => setEditing(true), title: "Edit video title, performers and studio", "aria-label": "Edit video title, performers and studio", className: "grid size-9 shrink-0 place-items-center rounded-xl border border-line-2 text-muted hover:bg-fg/5 hover:text-fg", children: _jsx(Icon, { name: "edit", className: "size-4" }) })] }), _jsxs("div", { className: "grid gap-4 sm:grid-cols-2", children: [_jsxs("div", { children: [_jsx("p", { className: "mb-2 text-xs font-semibold text-faint", children: "Performers" }), _jsxs("ul", { className: "grid gap-2", children: [item.performers.map((n) => {
                                                        const id = idOf(performers.data, n);
                                                        const inner = (_jsxs(_Fragment, { children: [_jsx("span", { className: "size-9 shrink-0 overflow-hidden rounded-full bg-surface-3", children: id ? _jsx("img", { src: withWidth(`/api/performers/${id}/image`, 240), alt: "", className: "size-full object-cover", onError: (e) => (e.currentTarget.src = '/static/icons/ax-placeholder.png') }) : _jsx("img", { src: "/static/icons/ax-placeholder.png", alt: "", className: "size-full object-cover" }) }), _jsxs("span", { className: "min-w-0 text-left", children: [_jsx("span", { className: "block truncate text-sm font-bold", children: n }), _jsx("span", { className: "text-xs text-faint", children: "Performer" })] })] }));
                                                        return _jsx("li", { children: id ? _jsx(Link, { to: "/performers/$id", params: { id: String(id) }, className: "flex items-center gap-3 rounded-xl border border-line bg-bg p-2 hover:bg-surface-2", children: inner }) : _jsx("div", { className: "flex items-center gap-3 rounded-xl border border-line bg-bg p-2", children: inner }) }, n);
                                                    }), !item.performers.length ? _jsx("li", { className: "text-sm text-muted", children: "Unknown performer" }) : null] })] }), _jsxs("div", { children: [_jsx("p", { className: "mb-2 text-xs font-semibold text-faint", children: "Studio" }), studio ? (_jsxs("div", { className: "flex items-center gap-3 rounded-xl border border-line bg-bg p-2", children: [_jsx("span", { className: "size-9 shrink-0 overflow-hidden rounded-lg bg-surface-3", children: _jsx("img", { src: idOf(studios.data, studio) ? withWidth(`/api/studios/${idOf(studios.data, studio)}/image`, 240) : '/static/icons/ax-placeholder.png', alt: "", className: "size-full object-cover", onError: (e) => (e.currentTarget.src = '/static/icons/ax-placeholder.png') }) }), _jsxs("span", { className: "min-w-0", children: [_jsx("span", { className: "block truncate text-sm font-bold", children: studio }), _jsx("span", { className: "text-xs uppercase text-faint", children: "Studio" })] })] })) : _jsx("p", { className: "text-sm text-muted", children: "No studio" })] })] })] }), item.tags.length ? (_jsxs("section", { className: CARD, "aria-label": "Scene tags", children: [_jsx("h2", { className: "mb-2 text-base font-bold", children: "Scene tags" }), _jsx("ul", { className: "flex flex-wrap gap-1.5", children: tags.map((t) => _jsx("li", { className: "rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-semibold text-accent", children: t }, t)) }), item.tags.length > 12 ? _jsx("button", { type: "button", onClick: () => setShowTags(!showTags), "aria-expanded": showTags, className: "mt-2 text-xs font-semibold text-muted hover:text-fg", children: showTags ? 'Show fewer' : `Show all ${item.tags.length}` }) : null] })) : null, _jsx(MarkersSection, { mediaId: item.media_id, onSeek: (t) => setSeekRequest((r) => ({ t, n: (r?.n ?? 0) + 1 })) }), _jsxs("section", { className: CARD, "aria-label": "Source", children: [_jsx("h2", { className: "mb-1 text-base font-bold", children: "Source" }), _jsxs("p", { className: "text-sm text-muted", children: [item.channel_title ?? 'Channel', " \u00B7 Telegram message ", item.id] })] }), _jsxs("section", { className: CARD, "aria-label": "Actions", children: [_jsx("h2", { className: "mb-3 text-base font-bold", children: "Actions" }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsxs("button", { type: "button", onClick: vlc, className: "inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-3.5 text-sm font-semibold hover:bg-fg/5", children: [_jsx(Icon, { name: "brand-vlc" }), "Open in VLC"] }), _jsxs("button", { type: "button", onClick: mpc, className: "inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-3.5 text-sm font-semibold hover:bg-fg/5", children: [_jsx(Icon, { name: "play" }), "Open in MPC-HC"] }), _jsxs("button", { type: "button", "aria-pressed": Boolean(item.favorite), disabled: fav.isPending, onClick: () => fav.mutate({ mediaId: item.media_id, favorite: !item.favorite }, { onError: () => toast.error('Could not update favourite') }), className: cn('inline-flex h-10 items-center gap-2 rounded-xl border px-3.5 text-sm font-semibold hover:bg-fg/5', item.favorite ? 'border-warn/60 text-warn' : 'border-line-2'), children: [_jsx(Icon, { name: "star" }), item.favorite ? 'Favourited' : 'Favourite'] })] })] })] }), _jsx(UpNext, { items: items, current: item })] }));
}
