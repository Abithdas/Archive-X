import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useChannels, useLibrary } from '@/api/hooks';
import { Button } from '@/design/Button';
import { AnimatePresence, cardMotion, motion } from '@/design/motion';
import { SkeletonGroup } from '@/design/Skeleton';
import { PageHead } from '@/design/PageHead';
import { cn } from '@/design/cn';
import { useSearch } from '@/app/search';
import { ActiveFilters } from './ActiveFilters';
import { ChannelEditor } from '@/features/channels/ChannelEditor';
import { ChannelMenu } from './ChannelMenu';
import { Pagination } from './Pagination';
import { Toolbar } from './Toolbar';
import { collectLibrary } from './collect';
import { SelectionBar } from './SelectionBar';
import { NO_LIBRARY_VIDEOS, collecting } from './shuffle';
import { useShuffle } from './useShuffle';
import { useSelection } from './selection';
import { VideoCard, VideoCardSkeleton } from './VideoCard';
import { useDebounced } from './useDebounced';
import { useLibraryStore } from './store';
const GRID = 'grid grid-cols-1 gap-4 min-[520px]:grid-cols-2 min-[900px]:grid-cols-3 min-[1200px]:grid-cols-4 min-[1700px]:grid-cols-5 min-[2100px]:grid-cols-6';
export function LibraryPage() {
    const { channels: picked, page, sort, favorites, filters, per, toggle, clear, setPage, setPer, resetFilters } = useLibraryStore();
    const selecting = useSelection((st) => st.active);
    useEffect(() => () => useSelection.getState().exit(), []); // leaving the page ends select mode
    const search = useDebounced(useSearch((s) => s.q).trim(), 300);
    const channels = useChannels();
    const qc = useQueryClient();
    const [refreshing, setRefreshing] = useState(false);
    const [editChannel, setEditChannel] = useState(null);
    const refresh = async () => {
        setRefreshing(true);
        try {
            await api('/api/telegram/refresh', { method: 'POST' });
            await qc.invalidateQueries({ queryKey: ['channels'] });
            toast('Channel list refreshed');
        }
        catch (e) {
            toast.error(e instanceof ApiError ? e.detail || 'Could not refresh channels' : 'Could not refresh channels');
        }
        finally {
            setRefreshing(false);
        }
    };
    const shuffle = useShuffle();
    const shuffleAll = (player) => shuffle.run(player, async () => {
        if (!selected.length)
            return [];
        const id = toast.loading(collecting(0));
        try {
            const { page: _p, per: _n, ...rest } = query;
            return await collectLibrary(rest, (n) => toast.loading(collecting(n), { id }));
        }
        finally {
            toast.dismiss(id);
        }
    }, NO_LIBRARY_VIDEOS);
    const selected = useMemo(() => (picked.length ? picked : (channels.data ?? []).map((c) => c.telegram_id)), [picked, channels.data]); // nothing picked means every channel
    const query = useMemo(() => ({ channels: picked, page, per, sort, favorites, q: search, ...filters }), [selected, page, per, sort, favorites, filters, search]);
    const library = useLibrary(query);
    const pages = library.data ? Math.max(1, Math.ceil(library.data.total / per)) : 1;
    return (_jsxs("section", { className: "mx-auto flex w-full max-w-[1800px] flex-1 flex-col pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(PageHead, { title: "Videos", blurb: "Browse and manage your video library" }), _jsx(Toolbar, { lead: _jsx(ChannelMenu, { channels: channels.data ?? [], selected: picked, onToggle: toggle, onAll: clear, onEdit: setEditChannel }), onRefresh: () => void refresh(), refreshing: refreshing, onShuffle: (pl) => void shuffleAll(pl), shuffling: shuffle.busy }), editChannel ? _jsx(ChannelEditor, { id: editChannel, onClose: () => setEditChannel(null) }) : null, library.isPending ? (_jsx(SkeletonGroup, { label: "Loading videos", className: GRID, children: Array.from({ length: 12 }, (_, i) => (_jsx(VideoCardSkeleton, { index: i }, i))) })) : library.isError ? (_jsxs("div", { className: "rounded-card border border-bad/40 p-6", role: "alert", children: [_jsx("p", { className: "font-semibold", children: "Could not load the library." }), _jsx("p", { className: "mb-3 text-sm text-muted", children: library.error instanceof ApiError ? library.error.detail : 'Network error' }), _jsx(Button, { onClick: () => library.refetch(), children: "Retry" })] })) : (_jsxs(_Fragment, { children: [_jsxs("h2", { className: "mb-4 flex items-baseline gap-2.5 text-xl font-extrabold tracking-tight", children: ["Library", _jsxs("span", { className: "text-[13px] font-normal text-muted", children: [library.data.total.toLocaleString(), " videos \u00B7 ", selected.length, " channel", selected.length === 1 ? '' : 's'] })] }), _jsx(ActiveFilters, {}), _jsx("div", { className: cn(GRID, library.isPlaceholderData && 'opacity-60 transition-opacity'), children: _jsx(AnimatePresence, { mode: "popLayout", children: library.data.items.map((item, i) => (_jsx(motion.div, { ...cardMotion(i), children: _jsx(VideoCard, { item: item }) }, `${item._cid}:${item.id}`))) }) }), library.data.items.length === 0 ? (_jsxs("div", { className: "grid place-items-center gap-3 py-12 text-center", children: [_jsx("p", { className: "text-muted", children: "No videos match these filters." }), _jsx(Button, { variant: "primary", onClick: resetFilters, children: "Reset filters" })] })) : null, selecting ? _jsx(SelectionBar, { visible: library.data.items }) : null, library.data.total > 0 ? _jsx(Pagination, { page: page, pages: pages, per: per, onPage: setPage, onPer: setPer }) : null] }))] }));
}
