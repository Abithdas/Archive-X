import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '@/api/client'
import type { ContentState, MarksPayload, Mode } from './content'

export const CONTENT_KEY = ['content-mode'] as const

export const useContentMode = () =>
  useQuery({ queryKey: CONTENT_KEY, queryFn: () => api<ContentState>('/api/content-mode'), staleTime: 60_000, retry: false })

/** Switch the mode. Everything on screen belongs to the old mode, so every other query is dropped and fetched again. */
export function useSwitchMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { mode: Mode; pin?: string; new_pin?: string }) => api<ContentState>('/api/content-mode', { method: 'POST', json: body }),
    onSuccess: async (state) => {
      qc.setQueryData(CONTENT_KEY, (old: ContentState | undefined) => ({ ...(old ?? { locked_for: 0 }), ...state }))
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== CONTENT_KEY[0] })
    },
  })
}

export function useChangePin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { current?: string; new: string }) => api<{ pin_set: boolean }>('/api/content-mode/pin', { method: 'POST', json: body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: CONTENT_KEY }),
  })
}

export const useChannelMarks = () =>
  useQuery({ queryKey: ['channel-marks'] as const, queryFn: () => api<MarksPayload>('/api/telegram/channel-marks'), staleTime: 10_000 })

export function useSetMarks() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { ids: string[]; in_library?: boolean; content_rating?: Mode; holds?: string }) => api<{ changed: number }>('/api/telegram/channel-marks', { method: 'POST', json: body }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['channel-marks'] })
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'channel-marks' && q.queryKey[0] !== CONTENT_KEY[0] })
    },
  })
}

export const errorText = (e: unknown, fallback: string) => (e instanceof ApiError && e.detail ? e.detail : fallback)

import type { BrowseResult, LocalFolder } from './content'

export const useLocalFolders = () =>
  useQuery({
    queryKey: ['local-folders'] as const,
    queryFn: () => api<{ folders: LocalFolder[]; mode: Mode }>('/api/local-folders'),
    staleTime: 5_000,
    refetchInterval: (q) => (q.state.data?.folders.some((f) => f.last_status === 'SCANNING' || f.scan?.running) ? 1500 : false),
  })

export const useBrowse = (path: string, enabled: boolean) =>
  useQuery({ queryKey: ['local-browse', path] as const, queryFn: () => api<BrowseResult>(`/api/local-folders/browse?path=${encodeURIComponent(path)}`), enabled, staleTime: 5_000, retry: false })

export function useLocalActions() {
  const qc = useQueryClient()
  const done = async () => {
    await qc.invalidateQueries({ queryKey: ['local-folders'] })
    await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'local-folders' && q.queryKey[0] !== CONTENT_KEY[0] && q.queryKey[0] !== 'local-browse' })
  }
  return {
    add: useMutation({ mutationFn: (b: { path: string; content_rating: Mode; holds?: string }) => api<{ id: number }>('/api/local-folders', { method: 'POST', json: b }), onSuccess: done }),
    edit: useMutation({ mutationFn: (v: { id: number; body: Record<string, unknown> }) => api(`/api/local-folders/${v.id}`, { method: 'POST', json: v.body }), onSuccess: done }),
    scan: useMutation({ mutationFn: (id: number) => api(`/api/local-folders/${id}/scan`, { method: 'POST', json: {} }), onSuccess: done }),
    remove: useMutation({ mutationFn: (id: number) => api(`/api/local-folders/${id}`, { method: 'DELETE' }), onSuccess: done }),
  }
}
