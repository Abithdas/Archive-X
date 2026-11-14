import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, libraryUrl } from './client';
import { scannedChannels } from '@/features/photos/photos';
export const queryKeys = {
    status: ['status'],
    channels: ['channels'],
    library: (q) => ['library', q],
};
export const useStatus = () => useQuery({ queryKey: queryKeys.status, queryFn: () => api('/api/status'), retry: false, staleTime: 5_000 });
export const useChannels = () => useQuery({ queryKey: queryKeys.channels, queryFn: () => api('/api/channels'), staleTime: 30_000 });
/** Every Telegram channel saved locally (the inventory), not only the ones published to the library. Used where a job picks Telegram channels. */
export const useSourceChannels = () => useQuery({ queryKey: ['source-channels'], queryFn: () => api('/api/source-channels'), staleTime: 30_000 });
/** Library page; keeps the previous page on screen while the next one loads (no flash), skeleton on first load. */
export const useLibrary = (query) => useQuery({
    queryKey: queryKeys.library(query),
    queryFn: () => api(libraryUrl(query)),
    enabled: query.channels.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
});
/** Whole performer or studio catalogue (the API caps at 5000); paging, search and filters run client-side. */
/** The catalogue list: the few fields the directory, pickers and name look-ups need (the full rows, with biographies, were megabytes). */
export const entitiesUrl = (kind) => `/api/${kind}?limit=5000&brief=1`;
/** Just the catalogue rows for names a page already knows (a video's performers, a performer's studios): a few rows instead of the whole list. */
export const lookupUrl = (kind, names) => `/api/entity-lookup?kind=${kind}&names=${encodeURIComponent(JSON.stringify(names))}`;
export const uniqueNames = (names) => [...new Set(names.map((n) => String(n ?? '').trim()).filter(Boolean))].sort().slice(0, 300);
export function useEntityLookup(kind, names) {
    const list = uniqueNames(names);
    return useQuery({ queryKey: ['entity-lookup', kind, list], queryFn: () => api(lookupUrl(kind, list)), enabled: list.length > 0, staleTime: 30_000 });
}
export const useEntities = (kind) => useQuery({ queryKey: ['entities', kind], queryFn: () => api(entitiesUrl(kind)), staleTime: 30_000 });
export const usePerformer = (id) => useQuery({
    queryKey: ['performer', id],
    queryFn: () => api(`/api/performers/${id}/videos`),
    select: (d) => ({
        performer: d.performer,
        videos: d.videos.map((v) => ({ ...v, _cid: v.source_channel, name: v.title || v.filename })),
    }),
    staleTime: 15_000,
});
export const useStorage = () => useQuery({ queryKey: ['storage'], queryFn: () => api('/api/settings/storage'), staleTime: 60_000, retry: false });
/** One studio and every video linked to it. */
export const useStudio = (id) => useQuery({
    queryKey: ['studio', id],
    queryFn: () => api(`/api/studios/${id}/videos`),
    select: (d) => ({
        site: { ...d.site, site_url: d.site.site_url ?? d.site_url ?? null },
        videos: d.videos.map((v) => ({ ...v, _cid: v.source_channel, name: v.title || v.filename })),
    }),
    staleTime: 15_000,
});
export const useProfile = () => useQuery({ queryKey: ['profile'], queryFn: () => api('/api/account/profile'), staleTime: 300_000, retry: false });
/** Names for the filter drawer's type-ahead (studios, performers, tags), fetched once and cached. */
export const useSuggestions = (enabled) => useQuery({
    queryKey: ['suggestions'],
    enabled,
    staleTime: 120_000,
    queryFn: async () => {
        const names = async (path) => (await api(path)).map((x) => x.name).filter(Boolean);
        const [studios, performers, tags] = await Promise.all([names('/api/studios?limit=240&brief=1'), names('/api/performers?limit=240&brief=1'), names('/api/tags?limit=320&brief=1')]);
        return { studios, performers, tags };
    },
});
/** True when the page is served by the desktop shell's backend (there is no web login to sign out of). */
export const useDesktopInfo = () => useQuery({
    queryKey: ['desktop-info'],
    queryFn: async () => {
        try {
            return (await api('/desktop/info')).desktop === true;
        }
        catch {
            return false;
        }
    },
    staleTime: Infinity,
});
/** One video by channel and Telegram message id (deep links and reloads have no list to look it up in). */
export const useVideo = (cid, mid) => useQuery({
    queryKey: ['video', cid, mid],
    queryFn: async () => {
        const r = await api(`/api/library?${new URLSearchParams({ page: '0', per: '1', channels: cid, sort: 'date_desc', message_id: String(mid) })}`);
        const hit = r.items.find((i) => i.id === mid && i._cid === cid);
        if (!hit)
            throw new ApiError(404, 'Video not found');
        return hit;
    },
    staleTime: 15_000,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
});
/** Favourite on/off for one library row, then refresh whatever lists show it. */
export function useFavorite() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (v) => api('/api/library/bulk-favorite', { method: 'POST', json: { media_ids: [v.mediaId], favorite: v.favorite } }),
        onSuccess: () => {
            for (const k of ['library', 'video', 'performer'])
                void qc.invalidateQueries({ queryKey: [k] });
        },
    });
}
/** Scene markers (chapters) of one library row. */
export const useMarkers = (mediaId) => useQuery({
    queryKey: ['markers', mediaId],
    enabled: !!mediaId,
    queryFn: async () => {
        const r = await api(`/api/media/${mediaId}/markers`);
        return Array.isArray(r) ? r : (r.markers ?? []);
    },
    staleTime: 60_000,
    retry: false,
});
/** The 36 sampled frame times, and whether their JPEGs are cached (only then can the timeline show previews). */
export const usePreviewManifest = (cid, mid, enabled = true) => useQuery({
    enabled,
    queryKey: ['preview-manifest', cid, mid],
    queryFn: () => api(`/api/play/preview-manifest/${encodeURIComponent(cid)}/${mid}`),
    staleTime: 60_000,
    retry: false,
});
/** Diagnostics summary (levels, watchdogs, incidents, HTTP latency); polled while the Operations page is open. */
export const useDiagnostics = () => useQuery({ queryKey: ['diagnostics'], queryFn: () => api('/api/diagnostics/summary'), refetchInterval: 5_000, staleTime: 4_000, retry: false });
/** Resource controller snapshot (playback, background lanes, limits). */
export const useResources = () => useQuery({ queryKey: ['resources'], queryFn: () => api('/api/operations/resources'), refetchInterval: 5_000, staleTime: 4_000, retry: false });
/** Live transfer lanes (governor slots, resources, lane jobs); polled while the Transfers page is open. */
export const useLanes = () => useQuery({ queryKey: ['lanes'], queryFn: () => api('/api/telegram-transfer/lanes'), refetchInterval: 2_500, staleTime: 2_000, retry: false });
/** Saved Content Automation settings (secrets are never returned, only whether they are saved). */
export const useAutomationConfig = () => useQuery({ queryKey: ['automation-config'], queryFn: () => api('/api/operations/automation/config'), staleTime: 30_000, retry: false });
/** Scanned photo channels: the saved channel list merged with each channel's photo-scan state. */
export const usePhotoChannels = () => useQuery({
    queryKey: ['photo-channels'],
    queryFn: async () => {
        const [saved, photo] = await Promise.all([
            api('/api/source-channels').catch(() => []),
            api('/api/photos/channels'),
        ]);
        return { savedCount: Array.isArray(saved) ? saved.length : 0, channels: scannedChannels(Array.isArray(saved) ? saved : [], photo) };
    },
    staleTime: 30_000,
});
const entityGet = (key, id, tail, fallback) => ({ queryKey: [key, id], queryFn: () => api(`/api/studios/${id}/${tail}`).catch(() => fallback), staleTime: 60_000, retry: false });
/** Provider metadata, related studios, career scene counts and pending video suggestions for one studio (a failure just leaves them empty). */
export const useStudioMetadata = (id) => useQuery(entityGet('studio-meta', id, 'metadata', { providers: [] }));
export const useStudioFamily = (id) => useQuery(entityGet('studio-family', id, 'family', { related: [] }));
export const useStudioCareer = (id) => useQuery(entityGet('studio-career', id, 'career-counts', { career_counts: [] }));
export const useSuggested = (kind, id) => useQuery({ queryKey: [`${kind}-suggested`, id], queryFn: async () => { const r = await api(`/api/${kind === 'studio' ? 'studios' : 'performers'}/${id}/suggested`).catch(() => []); return Array.isArray(r) ? r : []; }, staleTime: 0, retry: false });
export const useStudioSuggested = (id) => useSuggested('studio', id);
const perfGet = (key, id, tail, fallback) => ({ queryKey: [key, id], queryFn: () => api(`/api/performers/${id}/${tail}`).catch(() => fallback), staleTime: 60_000, retry: false });
/** Provider metadata, career scene counts and similar performers for one performer (a failure just leaves them empty). */
export const usePerformerMetadata = (id) => useQuery(perfGet('performer-meta', id, 'metadata', { providers: [] }));
export const usePerformerCareer = (id) => useQuery(perfGet('performer-career', id, 'career-counts', { career_counts: [] }));
export const usePerformerSimilar = (id, enabled) => useQuery({ queryKey: ['performer-similar', id], enabled, queryFn: () => api(`/api/performers/${id}/similar?limit=40`), staleTime: 60_000, retry: false });
/** Codec and container facts for one video (the smart-playback decision); fails quietly when Telegram is not connected. */
export const usePlayInfo = (cid, mid) => useQuery({
    queryKey: ['play-info', cid, mid],
    queryFn: () => api(`/api/play/info/${encodeURIComponent(cid)}/${mid}?wait=2`),
    staleTime: 60_000,
    retry: false,
});
