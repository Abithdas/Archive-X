import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, libraryUrl } from './client'
import type { Channel, Entity, EntityKind, LibraryItem, LibraryPage, PerformerDetail, LibraryQuery, Status } from './types'

export const queryKeys = {
  status: ['status'] as const,
  channels: ['channels'] as const,
  library: (q: LibraryQuery) => ['library', q] as const,
}

export const useStatus = () =>
  useQuery({ queryKey: queryKeys.status, queryFn: () => api<Status>('/api/status'), retry: false, staleTime: 5_000 })

export const useChannels = () =>
  useQuery({ queryKey: queryKeys.channels, queryFn: () => api<Channel[]>('/api/channels'), staleTime: 30_000 })

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
export const useEntities = (kind: EntityKind) =>
  useQuery({ queryKey: ['entities', kind] as const, queryFn: () => api<Entity[]>(`/api/${kind}?limit=5000`), staleTime: 30_000 })

export const usePerformer = (id: number) =>
  useQuery({
    queryKey: ['performer', id] as const,
    queryFn: () => api<PerformerDetail>(`/api/performers/${id}/videos`),
    select: (d) => ({
      performer: d.performer,
      videos: d.videos.map((v): LibraryItem => ({ ...v, _cid: v.source_channel, media_id: v.id, name: v.title || v.filename })),
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

export const useProfile = () =>
  useQuery({ queryKey: ['profile'] as const, queryFn: () => api<{ name: string; username: string }>('/api/account/profile'), staleTime: 300_000, retry: false })

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
