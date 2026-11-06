import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, libraryUrl } from './client'
import type { Channel, Entity, EntityKind, LibraryPage, LibraryQuery, Status } from './types'

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
