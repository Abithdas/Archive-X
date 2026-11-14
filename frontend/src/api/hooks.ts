import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, libraryUrl } from './client'
import type { Channel, Entity, EntityKind, LibraryItem, LibraryPage, PerformerDetail, LibraryQuery, Status } from './types'
import { scannedChannels } from '@/features/photos/photos'

export const queryKeys = {
  status: ['status'] as const,
  channels: ['channels'] as const,
  library: (q: LibraryQuery) => ['library', q] as const,
}

export const useStatus = () =>
  useQuery({ queryKey: queryKeys.status, queryFn: () => api<Status>('/api/status'), retry: false, staleTime: 5_000 })

export const useChannels = () =>
  useQuery({ queryKey: queryKeys.channels, queryFn: () => api<Channel[]>('/api/channels'), staleTime: 30_000 })

/** Every Telegram channel saved locally (the inventory), not only the ones published to the library. Used where a job picks Telegram channels. */
export const useSourceChannels = () =>
  useQuery({ queryKey: ['source-channels'] as const, queryFn: () => api<Channel[]>('/api/source-channels'), staleTime: 30_000 })

/** Library page; keeps the previous page on screen while the next one loads (no flash), skeleton on first load. */
export const useLibrary = (query: LibraryQuery) =>
  useQuery({
    queryKey: queryKeys.library(query),
    queryFn: () => api<LibraryPage>(libraryUrl(query)),
    enabled: query.channels.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  })

/** Whole performer or studio catalogue (the API caps at 5000); paging, search and filters run client-side. */
/** The catalogue list: the few fields the directory, pickers and name look-ups need (the full rows, with biographies, were megabytes). */
export const entitiesUrl = (kind: EntityKind) => `/api/${kind}?limit=5000&brief=1`
/** Just the catalogue rows for names a page already knows (a video's performers, a performer's studios): a few rows instead of the whole list. */
export const lookupUrl = (kind: EntityKind, names: readonly string[]) => `/api/entity-lookup?kind=${kind}&names=${encodeURIComponent(JSON.stringify(names))}`
export const uniqueNames = (names: ReadonlyArray<unknown>) => [...new Set(names.map((n) => String(n ?? '').trim()).filter(Boolean))].sort().slice(0, 300)
export function useEntityLookup(kind: EntityKind, names: ReadonlyArray<unknown>) {
  const list = uniqueNames(names)
  return useQuery({ queryKey: ['entity-lookup', kind, list] as const, queryFn: () => api<Entity[]>(lookupUrl(kind, list)), enabled: list.length > 0, staleTime: 30_000 })
}
export const useEntities = (kind: EntityKind) =>
  useQuery({ queryKey: ['entities', kind] as const, queryFn: () => api<Entity[]>(entitiesUrl(kind)), staleTime: 30_000 })

export const usePerformer = (id: number) =>
  useQuery({
    queryKey: ['performer', id] as const,
    queryFn: () => api<PerformerDetail>(`/api/performers/${id}/videos`),
    select: (d) => ({
      performer: d.performer,
      videos: d.videos.map((v): LibraryItem => ({ ...v, _cid: v.source_channel, name: v.title || v.filename })),
    }),
    staleTime: 15_000,
  })

export interface StorageSummary {
  files: number
  bytes: number
  disk?: { total: number; free: number; used: number }
}
export const useStorage = () =>
  useQuery({ queryKey: ['storage'] as const, queryFn: () => api<StorageSummary>('/api/settings/storage'), staleTime: 60_000, retry: false })

export interface StudioDetail { site: import('@/features/entities/studio').Site; videos: Array<Omit<LibraryItem, '_cid' | 'name'> & { source_channel: string; title: string | null; filename: string }>; site_url?: string }
/** One studio and every video linked to it. */
export const useStudio = (id: number) =>
  useQuery({
    queryKey: ['studio', id] as const,
    queryFn: () => api<StudioDetail>(`/api/studios/${id}/videos`),
    select: (d) => ({
      site: { ...d.site, site_url: d.site.site_url ?? d.site_url ?? null },
      videos: d.videos.map((v): LibraryItem => ({ ...v, _cid: v.source_channel, name: v.title || v.filename })),
    }),
    staleTime: 15_000,
  })

export const useProfile = () =>
  useQuery({ queryKey: ['profile'] as const, queryFn: () => api<import('@/features/profile/profile').Profile>('/api/account/profile'), staleTime: 300_000, retry: false })

/** Names for the filter drawer's type-ahead (studios, performers, tags), fetched once and cached. */
export const useSuggestions = (enabled: boolean) =>
  useQuery({
    queryKey: ['suggestions'] as const,
    enabled,
    staleTime: 120_000,
    queryFn: async () => {
      const names = async (path: string) => (await api<Array<{ name: string }>>(path)).map((x) => x.name).filter(Boolean)
      const [studios, performers, tags] = await Promise.all([names('/api/studios?limit=240&brief=1'), names('/api/performers?limit=240&brief=1'), names('/api/tags?limit=320&brief=1')])
      return { studios, performers, tags }
    },
  })

/** True when the page is served by the desktop shell's backend (there is no web login to sign out of). */
export const useDesktopInfo = () =>
  useQuery({
    queryKey: ['desktop-info'] as const,
    queryFn: async () => {
      try {
        return (await api<{ desktop: boolean }>('/desktop/info')).desktop === true
      } catch {
        return false
      }
    },
    staleTime: Infinity,
  })

/** One video by channel and Telegram message id (deep links and reloads have no list to look it up in). */
export const useVideo = (cid: string, mid: number) =>
  useQuery({
    queryKey: ['video', cid, mid] as const,
    queryFn: async () => {
      const r = await api<LibraryPage>(`/api/library?${new URLSearchParams({ page: '0', per: '1', channels: cid, sort: 'date_desc', message_id: String(mid) })}`)
      const hit = r.items.find((i) => i.id === mid && i._cid === cid)
      if (!hit) throw new ApiError(404, 'Video not found')
      return hit
    },
    staleTime: 15_000,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  })

/** Favourite on/off for one library row, then refresh whatever lists show it. */
export function useFavorite() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { mediaId: number; favorite: boolean }) => api('/api/library/bulk-favorite', { method: 'POST', json: { media_ids: [v.mediaId], favorite: v.favorite } }),
    onSuccess: () => {
      for (const k of ['library', 'video', 'performer']) void qc.invalidateQueries({ queryKey: [k] })
    },
  })
}

export interface MarkerRow { position: number; title: string; note?: string }
/** Scene markers (chapters) of one library row. */
export const useMarkers = (mediaId: number | undefined) =>
  useQuery({
    queryKey: ['markers', mediaId] as const,
    enabled: !!mediaId,
    queryFn: async () => {
      const r = await api<MarkerRow[] | { markers?: MarkerRow[] }>(`/api/media/${mediaId}/markers`)
      return Array.isArray(r) ? r : (r.markers ?? [])
    },
    staleTime: 60_000,
    retry: false,
  })

export interface PreviewManifest { ready: boolean; frames_ready?: boolean; frame_times: number[]; frame_count?: number; sprite_ready?: boolean; frame_width?: number; frame_height?: number; revision?: string; updated_at?: string | number | null }
/** The 36 sampled frame times, and whether their JPEGs are cached (only then can the timeline show previews). */
export const usePreviewManifest = (cid: string, mid: number, enabled = true) =>
  useQuery({
    enabled,
    queryKey: ['preview-manifest', cid, mid] as const,
    queryFn: () => api<PreviewManifest>(`/api/play/preview-manifest/${encodeURIComponent(cid)}/${mid}`),
    staleTime: 60_000,
    retry: false,
  })

/** Diagnostics summary (levels, watchdogs, incidents, HTTP latency); polled while the Operations page is open. */
export const useDiagnostics = () =>
  useQuery({ queryKey: ['diagnostics'] as const, queryFn: () => api<Record<string, unknown>>('/api/diagnostics/summary'), refetchInterval: 5_000, staleTime: 4_000, retry: false })

/** Resource controller snapshot (playback, background lanes, limits). */
export const useResources = () =>
  useQuery({ queryKey: ['resources'] as const, queryFn: () => api<{ resources?: Record<string, unknown> }>('/api/operations/resources'), refetchInterval: 5_000, staleTime: 4_000, retry: false })

export interface LanesPayload { governor?: Record<string, unknown>; resources?: Record<string, unknown>; jobs?: Array<Record<string, unknown>> }
/** Live transfer lanes (governor slots, resources, lane jobs); polled while the Transfers page is open. */
export const useLanes = () =>
  useQuery({ queryKey: ['lanes'] as const, queryFn: () => api<LanesPayload>('/api/telegram-transfer/lanes'), refetchInterval: 2_500, staleTime: 2_000, retry: false })

/** Saved Content Automation settings (secrets are never returned, only whether they are saved). */
export const useAutomationConfig = () =>
  useQuery({ queryKey: ['automation-config'] as const, queryFn: () => api<Record<string, unknown>>('/api/operations/automation/config'), staleTime: 30_000, retry: false })

/** Scanned photo channels: the saved channel list merged with each channel's photo-scan state. */
export const usePhotoChannels = () =>
  useQuery({
    queryKey: ['photo-channels'] as const,
    queryFn: async () => {
      const [saved, photo] = await Promise.all([
        api<import('@/features/photos/photos').PhotoChannel[]>('/api/source-channels').catch(() => []),
        api<import('@/features/photos/photos').PhotoChannel[]>('/api/photos/channels'),
      ])
      return { savedCount: Array.isArray(saved) ? saved.length : 0, channels: scannedChannels(Array.isArray(saved) ? saved : [], photo) }
    },
    staleTime: 30_000,
  })

const entityGet = <T,>(key: string, id: number, tail: string, fallback: T) =>
  ({ queryKey: [key, id] as const, queryFn: () => api<T>(`/api/studios/${id}/${tail}`).catch(() => fallback), staleTime: 60_000, retry: false })
/** Provider metadata, related studios, career scene counts and pending video suggestions for one studio (a failure just leaves them empty). */
export const useStudioMetadata = (id: number) => useQuery(entityGet<import('@/features/entities/studioExtrasLogic').Metadata>('studio-meta', id, 'metadata', { providers: [] }))
export const useStudioFamily = (id: number) => useQuery(entityGet<{ related?: import('@/features/entities/studioExtrasLogic').Related[] }>('studio-family', id, 'family', { related: [] }))
export const useStudioCareer = (id: number) => useQuery(entityGet<{ career_counts?: Array<{ provider?: string; count?: number }> }>('studio-career', id, 'career-counts', { career_counts: [] }))
export const useSuggested = (kind: 'studio' | 'performer', id: number) =>
  useQuery({ queryKey: [`${kind}-suggested`, id] as const, queryFn: async () => { const r = await api<unknown>(`/api/${kind === 'studio' ? 'studios' : 'performers'}/${id}/suggested`).catch(() => []); return Array.isArray(r) ? (r as import('@/features/entities/studioExtrasLogic').Suggested[]) : [] }, staleTime: 0, retry: false })
export const useStudioSuggested = (id: number) => useSuggested('studio', id)

const perfGet = <T,>(key: string, id: number, tail: string, fallback: T) =>
  ({ queryKey: [key, id] as const, queryFn: () => api<T>(`/api/performers/${id}/${tail}`).catch(() => fallback), staleTime: 60_000, retry: false })
/** Provider metadata, career scene counts and similar performers for one performer (a failure just leaves them empty). */
export const usePerformerMetadata = (id: number) => useQuery(perfGet<import('@/features/entities/studioExtrasLogic').Metadata>('performer-meta', id, 'metadata', { providers: [] }))
export const usePerformerCareer = (id: number) => useQuery(perfGet<{ career_counts?: Array<{ provider?: string; count?: number }> }>('performer-career', id, 'career-counts', { career_counts: [] }))
export const usePerformerSimilar = (id: number, enabled: boolean) =>
  useQuery({ queryKey: ['performer-similar', id] as const, enabled, queryFn: () => api<{ items?: Array<{ id: number; name: string; video_count?: number; image?: string | null } & import('@/features/entities/similarity').SimilarFields>; reason?: string }>(`/api/performers/${id}/similar?limit=40`), staleTime: 60_000, retry: false })

/** Codec and container facts for one video (the smart-playback decision); fails quietly when Telegram is not connected. */
export const usePlayInfo = (cid: string, mid: number) =>
  useQuery({
    queryKey: ['play-info', cid, mid] as const,
    queryFn: () => api<import('@/features/watch/smartstream').PlayInfo>(`/api/play/info/${encodeURIComponent(cid)}/${mid}?wait=2`),
    staleTime: 60_000,
    retry: false,
  })
