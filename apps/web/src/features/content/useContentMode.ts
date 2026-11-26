import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '@/api/client'
import { groupPatches, type ContentState, type MarksPayload, type Mode, type Patch } from './content'
import { swapModeData } from './modeSwap'

export const CONTENT_KEY = ['content-mode'] as const

export const useContentMode = () =>
  useQuery({ queryKey: CONTENT_KEY, queryFn: () => api<ContentState>('/api/content-mode'), staleTime: 60_000, retry: false })

/** Switch the mode. Everything on screen belongs to the old mode: see modeSwap.ts for what is put back, dropped and fetched again. */
export function useSwitchMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { mode: Mode; pin?: string; new_pin?: string }) => api<ContentState>('/api/content-mode', { method: 'POST', json: body }),
    onSuccess: (state) => {
      const from = qc.getQueryData<ContentState>(CONTENT_KEY)?.mode
      qc.setQueryData(CONTENT_KEY, (old: ContentState | undefined) => ({ ...(old ?? { locked_for: 0 }), ...state }))
      // Not awaited: the switch is done now. The screen shows what was last seen in the new mode (or a skeleton), and refreshes behind that.
      if (from) swapModeData(qc, from, state.mode)
      else void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== CONTENT_KEY[0] })
    },
  })
}

export function useChangePin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { current?: string; new?: string; remove?: boolean }) => api<{ pin_set: boolean }>('/api/content-mode/pin', { method: 'POST', json: body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: CONTENT_KEY }),
  })
}

export const useChannelMarks = () =>
  useQuery({ queryKey: ['channel-marks'] as const, queryFn: () => api<MarksPayload>('/api/telegram/channel-marks'), staleTime: 10_000 })

/** Save the marks of several channels or folders at once: one request for each different change. Pages refresh behind it, not in front of it. */
export function useSaveMarks() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (drafts: Record<string, Patch>) => {
      let changed = 0
      for (const g of groupPatches(drafts)) changed += (await api<{ changed: number }>('/api/telegram/channel-marks', { method: 'POST', json: { ids: g.ids, ...g.patch } })).changed
      return changed
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['channel-marks'] })
      void qc.invalidateQueries({ queryKey: ['local-folders'] })
      void qc.invalidateQueries({ predicate: (q) => !['channel-marks', 'local-folders', CONTENT_KEY[0]].includes(String(q.queryKey[0])) })
    },
  })
}

export const errorText = (e: unknown, fallback: string) => (e instanceof ApiError && e.detail ? e.detail : fallback)

import type { BrowseResult, LocalFolder } from './content'

export const useLocalFolders = () =>
  useQuery({
    queryKey: ['local-folders'] as const,
    queryFn: () => api<{ folders: LocalFolder[]; mode: Mode; hidden?: number }>('/api/local-folders'),
    staleTime: 5_000,
    refetchInterval: (q) => (q.state.data?.folders?.some((f) => f.last_status === 'SCANNING' || f.scan?.running) ? 1500 : false),
  })

export const useBrowse = (path: string, enabled: boolean) =>
  useQuery({ queryKey: ['local-browse', path] as const, queryFn: () => api<BrowseResult>(`/api/local-folders/browse?path=${encodeURIComponent(path)}`), enabled, staleTime: 5_000, retry: false })

export function useLocalActions() {
  const qc = useQueryClient()
  // Not awaited: a dialog or button waits for its own request only, never for every page on screen to load again.
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['local-folders'] })
    void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'local-folders' && q.queryKey[0] !== CONTENT_KEY[0] && q.queryKey[0] !== 'local-browse' })
  }
  return {
    add: useMutation({ mutationFn: (b: { path: string; content_rating: Mode; holds?: string }) => api<{ id: number }>('/api/local-folders', { method: 'POST', json: b }), onSuccess: done }),
    edit: useMutation({ mutationFn: (v: { id: number; body: Record<string, unknown> }) => api(`/api/local-folders/${v.id}`, { method: 'POST', json: v.body }), onSuccess: done }),
    scan: useMutation({ mutationFn: (id: number) => api(`/api/local-folders/${id}/scan`, { method: 'POST', json: {} }), onSuccess: done }),
    remove: useMutation({ mutationFn: (id: number) => api(`/api/local-folders/${id}`, { method: 'DELETE' }), onSuccess: done }),
  }
}
