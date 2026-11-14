import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { useEntityLookup, useStudio, useStudioCareer, useStudioFamily, useStudioMetadata, useStudioSuggested } from '@/api/hooks';
import { ApiError } from '@/api/client';
import { Button } from '@/design/Button';
import { Icon } from '@/design/Icon';
import { Skeleton, SkeletonGroup, SkeletonLine } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { NO_STUDIO_VIDEOS } from '@/features/library/shuffle';
import { useShuffle } from '@/features/library/useShuffle';
import { VideoCard, VideoCardSkeleton } from '@/features/library/VideoCard';
import { EntityEditor } from './EntityEditor';
import { NamedPoster, PerformerPoster, PosterGrid } from './PosterCard';
import { ACTION, ACTION_PRIMARY, Actions, BackBar, Bio, EditingBar, Eyebrow, EntityTabs, HeroCard, MEDIA_FRAME, Pill, Pills, Title, VideosHead, providerTone } from './EntityHero';
import { DetailsCard, SimilarPanel, SocialPanel, SuggestionsPanel } from './StudioExtras';
import { detailRows, providerBio, relatedList, similarStudios, socialLinks } from './studioextras';
import { FavoriteButton } from './FavoriteButton';
import { NOT_FOUND, TABS, bioText, imagePath, initial, linkedPerformers, metadataStatus, plural, providerPills, BIO_LIMIT } from './studio';
import { responsive } from '@/design/responsive';
import { Empty } from '@/design/Empty';
const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6';
const WRAP = 'mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10';
export function StudioPage({ id }) {
    const q = useStudio(id);
    const performers = useEntityLookup('performers', (q.data?.videos ?? []).flatMap((v) => (Array.isArray(v.performers) ? v.performers : [])));
    const studios = useEntityLookup('studios', (q.data?.videos ?? []).flatMap((v) => (Array.isArray(v.studios) ? v.studios : [])));
    const meta = useStudioMetadata(id);
    const family = useStudioFamily(id);
    const career = useStudioCareer(id);
    const suggested = useStudioSuggested(id);
    const [tab, setTab] = useState('videos');
    const [editing, setEditing] = useState(false);
    const [logoOk, setLogoOk] = useState(true);
    const shuffle = useShuffle();
    if (q.isPending)
        return (_jsx("section", { className: WRAP, children: _jsxs(SkeletonGroup, { label: "Loading studio", className: "grid gap-6", children: [_jsxs("div", { className: "flex gap-5 text-sm leading-snug", children: [_jsx(Skeleton, { className: "size-28 rounded-card sm:size-40" }), _jsxs("div", { className: "min-w-0 flex-1 text-3xl", children: [_jsx(SkeletonLine, { width: "40%" }), _jsxs("div", { className: "text-sm", children: [_jsx(SkeletonLine, { width: "25%" }), _jsx(SkeletonLine, { width: "85%" })] })] })] }), _jsx("div", { className: GRID, children: Array.from({ length: 8 }, (_, i) => _jsx(VideoCardSkeleton, {}, i)) })] }) }));
    if (q.isError) {
        const missing = q.error instanceof ApiError && q.error.status === 404;
        return (_jsxs("section", { className: cn(WRAP, 'grid place-items-center gap-3 text-center'), role: "alert", children: [_jsx("p", { className: "font-semibold", children: missing ? NOT_FOUND : 'Could not load this studio.' }), missing ? null : _jsx(Button, { variant: "primary", onClick: () => void q.refetch(), children: "Retry" }), _jsx(Link, { to: "/studios", className: "font-semibold text-accent underline", children: "Back to studios" })] }));
    }
    const { site, videos } = q.data;
    const linked = linkedPerformers(videos);
    const metadata = { ...(meta.data ?? { providers: [] }), career_counts: career.data?.career_counts ?? [] };
    const bio = bioText(site) || providerBio(meta.data);
    const pills = providerPills(site.provider_ids);
    const idOf = (name) => performers.data?.find((p) => p.name.toLowerCase() === name.toLowerCase())?.id;
    return (_jsxs("section", { className: WRAP, children: [_jsx(BackBar, { to: "/studios", section: "Studios", name: site.name }), _jsxs(HeroCard, { label: "Studio profile", media: _jsx("div", { className: cn(MEDIA_FRAME, 'grid place-items-center'), children: logoOk ? _jsx("img", { ...responsive(imagePath(site.id, site.image), '(max-width: 639px) 150px, 200px', [320, 480, 640]), alt: "", onError: () => setLogoOk(false), className: "size-full object-contain p-3.5" }) : _jsx("span", { className: "text-5xl font-extrabold text-muted", "aria-hidden": "true", children: initial(site.name) }) }), children: [_jsx(Eyebrow, { children: "Studio" }), _jsx(Title, { children: site.name }), _jsxs(Pills, { children: [_jsx(Pill, { children: plural(videos.length, 'video') }), _jsx(Pill, { children: plural(linked.length, 'performer') }), _jsx(Pill, { tone: metadataStatus(site) === 'Metadata linked' ? 'linked' : 'local', children: metadataStatus(site) }), pills.map((p) => _jsx(Pill, { tone: providerTone(p.tone), children: p.label }, p.label))] }), _jsx(Bio, { text: bio, limit: BIO_LIMIT }), _jsxs(Actions, { children: [_jsxs("button", { type: "button", disabled: shuffle.busy, onClick: () => void shuffle.run('mpc', () => videos, NO_STUDIO_VIDEOS), "aria-label": "Shuffle in MPC-HC", title: "Play a random video from this studio in MPC-HC", className: ACTION_PRIMARY, children: [_jsx(Icon, { name: "shuffle", className: "size-4" }), "Shuffle in MPC-HC"] }), _jsx(FavoriteButton, { kind: "studio", id: site.id, initial: Boolean(site.favorite) }), _jsxs("button", { type: "button", onClick: () => setEditing(true), title: "Edit studio details", "aria-label": "Edit studio details", className: ACTION, children: [_jsx(Icon, { name: "edit", className: "size-4" }), "Edit studio"] })] }), _jsx(SocialPanel, { links: socialLinks(site, meta.data), loading: meta.isPending })] }), _jsx(DetailsCard, { rows: detailRows(site, metadata, videos.length, linked.length), related: relatedList(family.data?.related) }), _jsx(EntityTabs, { label: "Studio details", value: tab, onChange: setTab, tabs: TABS.map((t) => ({ value: t.value, label: t.label, count: t.value === 'videos' ? videos.length : t.value === 'performers' ? linked.length : t.value === 'suggestions' ? (suggested.data?.length ?? 0) : 0 })) }), tab === 'similar' ? _jsx(SimilarPanel, { items: similarStudios(videos, site.name, studios.data ?? []) }) : tab === 'suggestions' ? _jsx(SuggestionsPanel, { kind: "studio", entityId: site.id, name: site.name }) : tab === 'videos' ? (videos.length ? _jsxs(_Fragment, { children: [_jsx(VideosHead, { kind: "studio", videos: videos }), _jsx("div", { className: GRID, role: "tabpanel", "aria-label": "Videos", children: videos.map((v) => _jsx(VideoCard, { item: v }, `${v._cid}:${v.id}`)) }), _jsx(EditingBar, { videos: videos })] }) : _jsx(Empty, { role: "tabpanel", "aria-label": "Videos", icon: "library", children: "No videos are linked to this studio yet." })) : linked.length ? (_jsx(PosterGrid, { label: "Performers", children: linked.map((p) => { const pid = idOf(p.name); return pid ? _jsx(PerformerPoster, { id: pid, name: p.name, count: p.count }, p.name) : _jsx(NamedPoster, { name: p.name, count: p.count }, p.name); }) })) : _jsx(Empty, { role: "tabpanel", "aria-label": "Performers", icon: "user", children: "No performers are linked to this studio's videos yet." }), editing ? _jsx(EntityEditor, { kind: "studios", id: site.id, onDone: () => setEditing(false) }) : null] }));
}
