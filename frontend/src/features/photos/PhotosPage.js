import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { aspectOf, assignColumns } from './masonry';
import { useColumnCount } from './useColumnCount';
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { usePhotoChannels } from '@/api/hooks';
import { useSearch } from '@/app/search';
import { Confirm } from '@/design/Confirm';
import { Icon } from '@/design/Icon';
import { Skeleton } from '@/design/Skeleton';
import { cn } from '@/design/cn';
import { Popover, PILL } from './Popover';
import { useDebounced } from '@/features/library/useDebounced';
import { useFaceGroups } from './FaceGroups';
import { PeopleRow } from './PeopleRow';
import { ContentTags, useContentSummary } from './ContentTags';
import { Empty } from '@/design/Empty';
import { motion, spring } from '@/design/motion';
import { PeopleView } from './PeopleView';
import { FaceReview } from './FaceReview';
import { DIFFERENT_NEEDS_GROUP, differentBody, differentCopy } from './faces';
import { ChannelEditor } from '@/features/channels/ChannelEditor';
import { PhotoCard } from './PhotoCard';
import { PhotoViewer } from './PhotoViewer';
import { EMPTY_INDEX, LOAD_FAILED, NO_CHANNELS_SAVED, NO_SCANNED, SORTS, channelAvatar, channelTitle, deleteBody, deleteCopy, deleteDone, deletePath, deleteStatus, emptyText, initials, photoName, photosUrl, pruneSelection, scanTitle, sortLabel, summaryText, toggleId } from './photos';
import { usePhotoStore } from './store';
const Tick = () => _jsx("span", { "aria-label": "selected", role: "img", className: "grid size-5 place-items-center rounded-full bg-good text-[11px] font-bold text-white", children: "\u2713" });
const reason = (e, fb) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb);
function Avatar({ channel }) {
    const [ok, setOk] = useState(true);
    const t = channelTitle(channel);
    return ok ? _jsx("img", { src: channelAvatar(channel), alt: "", loading: "lazy", onError: () => setOk(false), className: "size-full rounded-full object-cover" }) : _jsx("span", { "aria-hidden": "true", children: initials(t) });
}
export function PhotosPage() {
    const { selected, sort, favoritesOnly, favorites, tab, setTab, face, contentTags, setContentTags, setFace, setSelected, setSort, setFavoritesOnly, toggleFavorite } = usePhotoStore();
    const search = useDebounced(useSearch((s) => s.q).trim(), 300);
    const channels = usePhotoChannels();
    const qc = useQueryClient();
    const list = channels.data?.channels ?? [];
    const [viewer, setViewer] = useState(null);
    const [ask, setAsk] = useState(null);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState('');
    const [editChannel, setEditChannel] = useState(null);
    const sentinel = useRef(null);
    const [checking, setChecking] = useState(false);
    const effective = useMemo(() => (selected.length ? selected : list.map((c) => String(c.telegram_id))), [selected, list]);
    const hasContent = Boolean(useContentSummary().data?.total_photos);
    const channelButton = !selected.length ? 'All channels' : selected.length === 1 ? channelTitle(list.find((c) => String(c.telegram_id) === selected[0]) ?? { telegram_id: selected[0] }) : `${selected.length} channels`;
    const faces = useFaceGroups(selected, true, false, 24); // the busiest 24 people for the row, not everyone and not who they might be
    const [different, setDifferent] = useState(null);
    // A channel that is no longer scanned cannot stay selected.
    useEffect(() => { if (channels.data) {
        const keep = pruneSelection(selected, list);
        if (keep.length !== selected.length)
            setSelected(keep);
    } }, [channels.data]); // eslint-disable-line react-hooks/exhaustive-deps
    const favIds = useMemo(() => favorites, [favorites]);
    const q = useInfiniteQuery({
        queryKey: ['photos', effective, search, sort, favoritesOnly, favoritesOnly ? [...favIds].sort() : null, face, contentTags],
        enabled: effective.length > 0 && tab === 'library',
        initialPageParam: 0,
        queryFn: async ({ pageParam }) => {
            const r = await api(photosUrl({ channels: effective, q: search, sort, favorites: favoritesOnly, favoriteIds: favIds, page: pageParam, face, contentTags }));
            if (!r || r.ok !== true)
                throw new ApiError(0, r?.detail || LOAD_FAILED);
            return r;
        },
        getNextPageParam: (last, all) => (all.reduce((n, p) => n + p.items.length, 0) < last.total && last.items.length ? all.length : undefined),
        staleTime: 15_000,
    });
    const items = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
    const columnCount = useColumnCount();
    const columns = useMemo(() => assignColumns(items.map(aspectOf), columnCount), [items, columnCount]);
    const total = q.data?.pages[0]?.total ?? 0;
    const { hasNextPage, isFetchingNextPage, fetchNextPage } = q;
    const more = () => { if (hasNextPage && !isFetchingNextPage)
        void fetchNextPage(); };
    useEffect(() => {
        const el = sentinel.current;
        if (!el || typeof IntersectionObserver !== 'function')
            return;
        const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting))
            more(); }, { rootMargin: '900px 0px' });
        io.observe(el);
        return () => io.disconnect();
    });
    const remove = async () => {
        if (!ask)
            return;
        const { photo, remote } = ask;
        setBusy(true);
        setNote(deleteStatus(remote, photoName(photo)));
        try {
            const r = await api(deletePath(photo), { method: 'POST', json: deleteBody(remote) });
            if (!r || r.ok !== true)
                throw new ApiError(0, r?.detail || 'Photo deletion failed.');
            setAsk(null);
            setNote(deleteDone(remote, r));
            toast(deleteDone(remote, r));
            await Promise.all([qc.invalidateQueries({ queryKey: ['photos'] }), qc.invalidateQueries({ queryKey: ['photo-channels'] })]);
        }
        catch (e) {
            const m = reason(e, 'Photo deletion failed. The library entry was retained.');
            setAsk(null);
            setNote(m);
            toast.error(m);
        }
        finally {
            setBusy(false);
        }
    };
    const separate = async () => {
        const p = different;
        if (!p || !face)
            return;
        const body = differentBody(p.id, face, faces.data?.groups ?? []);
        setDifferent(null);
        if (!body)
            return void setNote(DIFFERENT_NEEDS_GROUP);
        setBusy(true);
        setNote(`Separating ${photoName(p)} from the current face group…`);
        try {
            const r = await api('/api/photos/faces/different-person', { method: 'POST', json: body });
            if (!r || r.ok !== true)
                throw new ApiError(0, r?.detail || 'Could not separate this photo from the face group.');
            const m = r.message || 'Moved the face detection(s) into new unassigned group(s).';
            setNote(m);
            toast(m);
            await Promise.all([qc.invalidateQueries({ queryKey: ['photos'] }), qc.invalidateQueries({ queryKey: ['face-groups'] })]);
        }
        catch (e) {
            const m = reason(e, 'Could not separate this photo from the face group.');
            setNote(m);
            toast.error(m);
        }
        finally {
            setBusy(false);
        }
    };
    const refresh = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['photo-channels'] }), qc.invalidateQueries({ queryKey: ['photos'] })]); toast('Photo library refreshed'); };
    const empty = emptyText(search, favoritesOnly);
    const status = channels.isError ? reason(channels.error, 'Photo scan status is unavailable.')
        : note || (channels.data && !list.length ? (channels.data.savedCount ? NO_SCANNED : NO_CHANNELS_SAVED) : q.isSuccess && !total ? EMPTY_INDEX : '');
    return (_jsxs("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsxs("div", { className: "mb-4 flex flex-wrap items-baseline gap-x-3", children: [_jsx("h1", { className: "text-2xl font-bold", children: "Photos" }), tab === 'library' ? _jsx("span", { role: "status", "aria-live": "polite", className: "text-sm text-muted", children: channels.isPending ? 'Loading…' : status || summaryText(total) }) : null] }), _jsxs("div", { className: "mb-3 flex flex-wrap items-center gap-2.5", role: "group", "aria-label": "Photo library tools", children: [_jsx("div", { role: "tablist", "aria-label": "Photos views", className: "inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]", children: [['library', 'Photos'], ['faces', 'People']].map(([v, label]) => (_jsxs("button", { type: "button", role: "tab", "aria-selected": tab === v, onClick: () => setTab(v), className: cn('relative h-9 rounded-[9px] px-4 text-sm font-semibold transition-colors', tab === v ? 'text-fg' : 'text-muted hover:text-fg'), children: [tab === v ? _jsx(motion.span, { layoutId: "photos-view-pill", transition: spring, "aria-hidden": "true", className: "absolute inset-0 rounded-[9px] bg-surface-3" }) : null, _jsx("span", { className: "relative", children: label })] }, v))) }), tab === 'library' ? (_jsxs(_Fragment, { children: [_jsxs(Popover, { label: channelButton, icon: _jsx(Icon, { name: "grid", className: "size-4" }), active: selected.length > 0, children: [_jsxs("div", { role: "group", "aria-label": "Channels", className: "grid gap-0.5", children: [_jsxs("button", { type: "button", disabled: !list.length, onClick: () => setSelected([]), "aria-pressed": !selected.length, className: "flex items-center gap-3 rounded-xl px-2 py-1.5 text-left text-sm font-semibold hover:bg-fg/5", children: [_jsx("span", { className: "grid size-8 place-items-center rounded-full bg-surface-3", children: _jsx(Icon, { name: "grid", className: "size-4" }) }), _jsx("span", { className: "flex-1", children: "All channels" }), _jsx("span", { className: "text-xs text-muted", children: list.reduce((n, c) => n + Number(c.photo_count || 0), 0).toLocaleString() }), !selected.length ? _jsx(Tick, {}) : _jsx("span", { className: "size-5" })] }), list.map((c) => {
                                                const id = String(c.telegram_id);
                                                const on = selected.includes(id);
                                                return (_jsxs("button", { type: "button", "aria-pressed": on, title: scanTitle(c), onClick: () => setSelected(toggleId(selected, id)), className: "flex items-center gap-3 rounded-xl px-2 py-1.5 text-left text-sm hover:bg-fg/5", children: [_jsx("span", { className: "grid size-8 shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 text-xs font-bold", children: _jsx(Avatar, { channel: c }) }), _jsx("span", { className: "min-w-0 flex-1 truncate", children: channelTitle(c) }), _jsx("span", { className: "text-xs text-muted", children: Number(c.photo_count || 0).toLocaleString() }), on ? _jsx(Tick, {}) : _jsx("span", { className: "size-5" })] }, id));
                                            })] }), selected.length === 1 ? _jsxs("button", { type: "button", onClick: () => setEditChannel(selected[0] ?? null), "aria-label": "Edit selected channel", className: "mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5", children: [_jsx(Icon, { name: "edit", className: "size-3.5" }), "Edit channel"] }) : null] }), _jsxs("label", { className: cn(PILL, 'relative cursor-pointer pr-9'), children: [_jsx(Icon, { name: "sort" }), _jsx("span", { children: sortLabel(sort) }), _jsx(Icon, { name: "chevron-down", className: "absolute right-3 size-4 text-muted" }), _jsx("select", { value: sort, onChange: (e) => setSort(e.target.value), "aria-label": "Sort photos", className: "absolute inset-0 cursor-pointer opacity-0", children: SORTS.map((s) => _jsx("option", { value: s.value, children: s.label }, s.value)) })] }), hasContent ? _jsx(Popover, { label: "Filters", icon: _jsx(Icon, { name: "filter", className: "size-4" }), badge: contentTags.length, active: contentTags.length > 0, children: _jsx(ContentTags, { bare: true }) }) : null, _jsxs("button", { type: "button", "aria-pressed": favoritesOnly, onClick: () => setFavoritesOnly(!favoritesOnly), title: "Show only photos starred in this browser", className: cn(PILL, favoritesOnly && 'border-warn text-warn'), children: [_jsx("span", { "aria-hidden": "true", children: favoritesOnly ? '★' : '☆' }), "Favourites"] }), _jsx("button", { type: "button", onClick: () => void refresh(), "aria-label": "Refresh photo library", title: "Refresh channels and the indexed photo library", className: cn(PILL, 'w-10 justify-center px-0'), children: _jsx(Icon, { name: "refresh" }) })] })) : null] }), editChannel ? _jsx(ChannelEditor, { id: editChannel, onClose: () => setEditChannel(null) }) : null, tab === 'library' && face ? (_jsxs("div", { role: "region", "aria-label": "Photos of one person", className: "mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-accent/30 bg-accent/10 px-4 py-3", children: [_jsxs("button", { type: "button", onClick: () => { setFace(null); setTab('faces'); }, className: "inline-flex h-9 items-center gap-1.5 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg", children: [_jsx(Icon, { name: "arrow-left", className: "size-4" }), "Back to People"] }), _jsx("span", { className: "min-w-0 text-sm font-semibold [overflow-wrap:anywhere]", children: face.scope ? face.label : `Photos of ${face.label}` }), !face.scope ? _jsx("button", { type: "button", onClick: () => setChecking(true), className: "inline-flex h-9 items-center rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5", children: "Check faces" }) : null, _jsx("button", { type: "button", onClick: () => setFace(null), className: "ml-auto text-xs font-semibold text-accent hover:underline", children: "Show all photos" })] })) : null, checking && face && !face.scope ? _jsx(FaceReview, { performerId: face.performerId, groupIds: face.groupIds.length ? face.groupIds : face.groupId && face.groupId > 0 ? [face.groupId] : [], label: face.label, onClose: () => setChecking(false) }) : null, tab === 'faces' ? _jsx(PeopleView, {}) : null, _jsxs("div", { hidden: tab === 'faces', children: [contentTags.length ? (_jsxs("div", { role: "group", "aria-label": "Active filters", className: "mb-3 flex flex-wrap items-center gap-2", children: [contentTags.map((t) => _jsxs("button", { type: "button", onClick: () => setContentTags(contentTags.filter((x) => x !== t)), "aria-label": `Remove filter ${t}`, className: "inline-flex h-7 items-center gap-1.5 rounded-full bg-accent/15 px-3 text-xs font-semibold text-accent", children: [t.replace(/[_-]/g, ' '), _jsx("span", { "aria-hidden": "true", children: "\u00D7" })] }, t)), _jsx("button", { type: "button", onClick: () => setContentTags([]), className: "text-xs font-semibold text-accent hover:underline", children: "Clear all" })] })) : null, _jsx(PeopleRow, { groups: faces.data?.groups ?? [], onManage: () => setTab('faces') }), !effective.length ? (channels.isPending ? null : _jsx(Empty, { icon: "images", title: "No photos yet", children: status })) : q.isPending ? (_jsx("div", { role: "status", "aria-label": "Loading photos", className: "columns-2 gap-2 sm:columns-3 sm:gap-3 lg:columns-4 xl:columns-6", children: [236, 318, 188, 272, 214, 344, 198, 286].map((h, i) => _jsx(Skeleton, { style: { height: h }, className: "mb-2 break-inside-avoid rounded-xl sm:mb-3" }, i)) })) : q.isError ? (_jsxs("div", { role: "alert", className: "rounded-card border border-bad/40 p-6", children: [_jsx("p", { className: "font-semibold", children: "Photo library could not be loaded." }), _jsx("p", { className: "mt-1 text-sm text-muted", children: reason(q.error, 'Check the Operations log and try again.') }), _jsx("button", { type: "button", onClick: () => void q.refetch(), className: "mt-3 h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg", children: "Retry" })] })) : !items.length ? (_jsx(Empty, { icon: "images", title: empty.title, children: empty.body })) : (_jsx("div", { className: "flex gap-2 sm:gap-3", "aria-label": "Photos in selected channels", children: columns.map((col, c) => (_jsx("div", { className: "flex min-w-0 flex-1 flex-col", children: col.map((i) => { const p = items[i]; return _jsx(PhotoCard, { photo: p, favorite: favorites.has(String(p.id)), onOpen: () => setViewer(i), onRemove: (remote) => setAsk({ photo: p, remote }), onDifferent: face ? () => setDifferent(p) : undefined }, `${p.channel_id}:${p.message_id}`); }) }, c))) })), _jsx("div", { ref: sentinel, "aria-hidden": "true", className: "h-8", hidden: !hasNextPage, children: isFetchingNextPage ? _jsx("span", { className: "mx-auto block size-5 animate-spin rounded-full border-2 border-line border-t-accent" }) : null }), hasNextPage ? _jsx("div", { className: "text-center", children: _jsx("button", { type: "button", onClick: more, disabled: isFetchingNextPage, className: "h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Load more" }) }) : null] }), viewer !== null && items[viewer] ? _jsx(PhotoViewer, { items: items, index: viewer, total: total, favorites: favorites, onIndex: setViewer, onClose: () => setViewer(null), onFavorite: toggleFavorite, onNeedMore: more }) : null, different ? _jsx(Confirm, { title: "Different person?", text: differentCopy, ok: "Separate photo", danger: true, busy: busy, onCancel: () => setDifferent(null), onOk: () => void separate() }) : null, ask ? (() => { const c = deleteCopy(ask.remote, photoName(ask.photo), ask.photo.channel_title || 'this channel'); return _jsx(Confirm, { title: c.title, text: c.text, ok: c.ok, danger: true, busy: busy, onCancel: () => setAsk(null), onOk: () => void remove() }); })() : null] }));
}
