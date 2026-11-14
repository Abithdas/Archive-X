import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Link } from '@tanstack/react-router';
import { useReducer, useState } from 'react';
import { useEntityLookup, usePerformer, usePerformerCareer, usePerformerMetadata, usePerformerSimilar, useSuggested } from '@/api/hooks';
import { ApiError } from '@/api/client';
import { Button } from '@/design/Button';
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton';
import { EntityEditor } from './EntityEditor';
import { ACTION, ACTION_PRIMARY, Actions, BackBar, Bio, EditingBar, Eyebrow, EntityTabs, HeroCard, MEDIA_FRAME, Pill, Pills, Title, VideosHead, providerTone } from './EntityHero';
import { PortraitAdjust } from './PortraitAdjust';
import { imageStyle, readCrop } from './portrait';
import { NO_PERFORMER_VIDEOS } from '@/features/library/shuffle';
import { useShuffle } from '@/features/library/useShuffle';
import { VideoCard, VideoCardSkeleton } from '@/features/library/VideoCard';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { FavoriteButton } from './FavoriteButton';
import { DetailsCard, SocialPanel, SuggestionsPanel } from './StudioExtras';
import { PTABS, detailRows, isUnknownLocal, metadataLinked, similarEmpty, studioCount, studiosFor, STUDIOS_EMPTY } from './performerextras';
import { providerPills } from './studio';
import { providerBio, socialLinks } from './studioextras';
import { PerformerPoster, PosterGrid, StudioPoster } from './PosterCard';
import { responsive } from '@/design/responsive';
import { Empty } from '@/design/Empty';
const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6';
const BIO_LIMIT = 280;
export function PerformerPage({ id }) {
    const q = usePerformer(id);
    const [imgOk, setImgOk] = useState(true);
    const shuffle = useShuffle();
    const [editing, setEditing] = useState(false);
    const [adjusting, setAdjusting] = useState(false);
    const [, refreshFraming] = useReducer((n) => n + 1, 0);
    const [tab, setTab] = useState('videos');
    const studios = useEntityLookup('studios', (q.data?.videos ?? []).flatMap((v) => (Array.isArray(v.studios) ? v.studios : [])));
    const meta = usePerformerMetadata(id);
    const career = usePerformerCareer(id);
    const suggested = useSuggested('performer', id);
    const similar = usePerformerSimilar(id, tab === 'similar');
    if (q.isPending)
        return (_jsx("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: _jsxs(SkeletonGroup, { label: "Loading performer", className: "grid gap-6", children: [_jsxs("div", { className: "flex gap-5 text-sm leading-snug", children: [_jsx(Skeleton, { className: "aspect-[3/4] w-32 rounded-card sm:w-44" }), _jsxs("div", { className: "min-w-0 flex-1 text-3xl", children: [_jsx(SkeletonLine, { width: "45%" }), _jsxs("div", { className: "text-sm", children: [_jsx(SkeletonLine, { width: "25%" }), _jsx(SkeletonLine, { width: "90%" }), _jsx(SkeletonLine, { width: "70%" })] })] })] }), _jsx("div", { className: GRID, children: Array.from({ length: 8 }, (_, i) => _jsx(VideoCardSkeleton, {}, i)) })] }) }));
    if (q.isError) {
        const missing = q.error instanceof ApiError && q.error.status === 404;
        return (_jsxs("section", { role: "alert", className: "mx-auto grid max-w-xl place-items-center gap-3 p-10 text-center", children: [_jsx("p", { className: "font-semibold", children: missing ? 'Performer not found.' : 'Could not load this performer.' }), missing ? null : _jsx(Button, { variant: "primary", onClick: () => void q.refetch(), children: "Retry" }), _jsx(Link, { to: "/performers", className: "font-semibold text-accent underline", children: "Back to performers" })] }));
    }
    const { performer: p, videos } = q.data;
    const unknown = isUnknownLocal(p);
    const metadata = { ...(meta.data ?? { providers: [] }), career_counts: career.data?.career_counts ?? [] };
    const bio = (p.bio ?? '').trim() || (unknown ? 'Local fallback profile for videos without a confirmed performer.' : providerBio(meta.data));
    const linked = !unknown && metadataLinked(p, meta.data);
    const pills = unknown ? [] : providerPills(p.provider_ids);
    const crop = readCrop(p.id);
    const imgSrc = `/api/performers/${p.id}/image?v=${encodeURIComponent(p.image ?? '')}`;
    return (_jsxs("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(BackBar, { to: "/performers", section: "Performers", name: p.name }), _jsxs(HeroCard, { label: "Performer profile", media: _jsx("div", { className: cn(MEDIA_FRAME, 'group'), children: imgOk ? (_jsxs("button", { type: "button", onClick: () => setAdjusting(true), "aria-label": `Adjust portrait framing for ${p.name}`, title: "Adjust portrait framing", "aria-haspopup": "dialog", className: "absolute inset-0 block cursor-pointer outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-accent", children: [_jsx("img", { ...responsive(imgSrc, '(max-width: 639px) 150px, 200px', [320, 480, 640, 960]), alt: "", onError: () => setImgOk(false), style: imageStyle(crop), className: "pointer-events-none size-full select-none object-cover transition-[filter] group-hover:brightness-110" }), _jsxs("span", { "aria-hidden": "true", className: "pointer-events-none absolute bottom-[15px] left-1/2 inline-flex h-8 max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full border border-line-2 bg-bg/80 px-2.5 text-[11.5px] font-semibold text-fg shadow-[0_4px_15px_rgb(0_0_0/0.24)] backdrop-blur-[10px]", children: [_jsxs("svg", { viewBox: "0 0 24 24", className: "size-[15px]", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("path", { d: "M6 2v14a2 2 0 0 0 2 2h14" }), _jsx("path", { d: "M18 22V8a2 2 0 0 0-2-2H2" })] }), "Adjust framing"] })] })) : (_jsx("div", { className: "grid h-full place-items-center text-5xl font-extrabold text-muted", "aria-hidden": "true", children: p.name.charAt(0).toUpperCase() })) }), children: [_jsx(Eyebrow, { children: "Performer" }), _jsx(Title, { children: p.name }), _jsxs(Pills, { children: [p.gender ? _jsx(Pill, { children: _jsx("span", { className: "capitalize", children: p.gender }) }) : null, _jsx(Pill, { children: videos.length === 1 ? '1 video' : `${videos.length} videos` }), _jsx(Pill, { tone: linked ? 'linked' : 'local', children: linked ? 'Metadata linked' : 'Local profile' }), pills.map((x) => _jsx(Pill, { tone: providerTone(x.tone), children: x.label }, x.label))] }), p.profile && !p.profile.startsWith('http') ? _jsx("p", { className: "mt-2 text-[13px] text-muted", children: p.profile }) : null, _jsx(Bio, { text: bio, limit: BIO_LIMIT }), _jsxs(Actions, { children: [_jsxs("button", { type: "button", disabled: shuffle.busy, onClick: () => void shuffle.run('mpc', () => videos, NO_PERFORMER_VIDEOS), "aria-label": "Shuffle in MPC-HC", title: "Play a random video of this performer in MPC-HC", className: ACTION_PRIMARY, children: [_jsx(Icon, { name: "shuffle", className: "size-4" }), "Shuffle in MPC-HC"] }), _jsx(FavoriteButton, { kind: "performer", id: p.id, initial: Boolean(p.favorite) }), _jsxs("button", { type: "button", onClick: () => setEditing(true), title: "Edit performer details", "aria-label": "Edit performer details", className: ACTION, children: [_jsx(Icon, { name: "edit", className: "size-4" }), "Edit profile"] })] }), _jsx(SocialPanel, { label: "Social media links", links: socialLinks({ profile: p.profile?.startsWith('http') ? p.profile : null }, meta.data), loading: meta.isPending && !unknown })] }), editing ? _jsx(EntityEditor, { kind: "performers", id: p.id, onDone: () => setEditing(false) }) : null, adjusting ? _jsx(PortraitAdjust, { id: p.id, name: p.name, src: imgSrc, open: true, onClose: () => setAdjusting(false), onSaved: refreshFraming }) : null, _jsx(DetailsCard, { label: "Performer details", rows: detailRows(p, metadata, videos.length), related: [] }), _jsx(EntityTabs, { label: "Performer details", value: tab, onChange: setTab, tabs: PTABS.map((t) => ({ value: t.value, label: t.label, count: t.value === 'videos' ? videos.length : t.value === 'studios' ? studioCount(videos) : t.value === 'suggestions' ? (suggested.data?.length ?? 0) : 0 })) }), tab === 'videos' ? (videos.length ? _jsxs(_Fragment, { children: [_jsx(VideosHead, { kind: "performer", videos: videos }), _jsx("div", { className: GRID, role: "tabpanel", "aria-label": "Videos", children: videos.map((v) => _jsx(VideoCard, { item: v }, `${v._cid}:${v.id}`)) }), _jsx(EditingBar, { videos: videos })] }) : _jsx(Empty, { role: "tabpanel", "aria-label": "Videos", icon: "library", children: "No videos for this performer yet." })) : tab === 'studios' ? ((() => {
                const items = studiosFor(videos, studios.data ?? []);
                return items.length ? (_jsx(PosterGrid, { label: "Studios", children: items.map((s) => _jsx(StudioPoster, { id: s.id, name: s.name, image: s.image, count: s.count }, s.id)) })) : _jsx(Empty, { role: "tabpanel", "aria-label": "Studios", icon: "studio", children: STUDIOS_EMPTY });
            })()) : tab === 'similar' ? (similar.isPending ? _jsx("p", { role: "tabpanel", "aria-label": "Similar Performers", className: "py-8 text-center text-sm text-muted", children: "Finding similar performers\u2026" })
                : (similar.data?.items ?? []).length ? _jsx(PerformerGrid, { items: similar.data.items })
                    : _jsx(Empty, { role: "tabpanel", "aria-label": "Similar Performers", icon: "user", children: similarEmpty(similar.data?.reason) })) : _jsx(SuggestionsPanel, { kind: "performer", entityId: p.id, name: p.name })] }));
}
function PerformerGrid({ items }) {
    return _jsx(PosterGrid, { label: "Similar Performers", children: items.map((x) => _jsx(PerformerPoster, { id: x.id, name: x.name, image: x.image, count: x.video_count ?? 0, similar: x }, x.id)) });
}
