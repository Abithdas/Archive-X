import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

export type ItemState = 'WANTED' | 'DOWNLOADING' | 'DONE' | 'SKIPPED' | 'FAILED' | 'PAUSED'
export interface CollectionItem { id: number; season: number; episode: number; name: string; label: string; air_date: string; state: ItemState; release: string; note: string; got: number; total: number; attempts: number }
export interface CollectionTitle { id: number; kind: 'movie' | 'series'; imdb: string; title: string; year: number | null; poster: string; scope: 'all' | 'new'; monitored: boolean; /** Only a home for a download chosen by hand (nothing else of it is looked for). */ manual?: boolean; counts: Record<'WANTED' | 'DOWNLOADING' | 'DONE' | 'SKIPPED', number> & { FAILED?: number; PAUSED?: number }; items: CollectionItem[] }
export interface CollectionSettings { on: boolean; dir: string; quality: number; max_gb_movie: number; max_gb_episode: number; /** How many downloads run at once (1 to 4). */ parallel: number }
export interface CollectionState { settings: CollectionSettings; titles: CollectionTitle[]; server: { up: boolean; ready: boolean; installed: boolean }; running: boolean }
export const collectionKey = ['collection'] as const

const NO_SETTINGS: CollectionSettings = { on: false, dir: '', quality: 1080, max_gb_movie: 12, max_gb_episode: 4, parallel: 1 }
/** Whatever the server sent, in the shape the page reads: a missing part is an empty one rather than an error. */
export const normalise = (d: Partial<CollectionState> | null | undefined): CollectionState => ({
  settings: { ...NO_SETTINGS, ...(d?.settings ?? {}) }, titles: Array.isArray(d?.titles) ? d!.titles : [], server: d?.server ?? { up: true, ready: false, installed: false }, running: Boolean(d?.running),
})

/** The Collection as the server has it, refreshed while something is being fetched. */
export const useCollection = (enabled = true) => useQuery({
  queryKey: collectionKey, enabled, retry: false, staleTime: 3_000,
  queryFn: () => api<Partial<CollectionState>>('/api/collection').then(normalise),
  refetchInterval: (q) => (q.state.data?.titles.some((t) => t.counts?.DOWNLOADING > 0) || q.state.data?.running ? 3_000 : 30_000),
})

export function useCollectionActions() {
  const qc = useQueryClient()
  const done = () => qc.invalidateQueries({ queryKey: collectionKey })
  return {
    add: useMutation({ mutationFn: (b: { kind: 'movie' | 'series'; imdb: string; title?: string; year?: number | null; poster?: string; scope?: 'all' | 'new' }) => api('/api/collection/titles', { method: 'POST', json: b }), onSuccess: done }),
    remove: useMutation({ mutationFn: (id: number) => api(`/api/collection/titles/${id}`, { method: 'DELETE' }), onSuccess: done }),
    settings: useMutation({ mutationFn: (b: Partial<CollectionSettings>) => api('/api/collection/settings', { method: 'POST', json: b }), onSuccess: done }),
    item: useMutation({ mutationFn: (b: { id: number; action: 'retry' | 'skip' | 'pause' | 'resume' }) => api(`/api/collection/items/${b.id}/${b.action}`, { method: 'POST', json: {} }), onSuccess: done }),
    /** Take one download out of the list (a running one is stopped; a finished file stays in the library). */
    deleteItem: useMutation({ mutationFn: (id: number) => api(`/api/collection/items/${id}`, { method: 'DELETE' }), onSuccess: done }),
    run: useMutation({ mutationFn: () => api('/api/collection/run', { method: 'POST', json: {} }), onSuccess: done }),
  }
}

/** "3 of 8 in your library · 2 downloading · 3 waiting", the way a person reads it. */
export function summary(t: Pick<CollectionTitle, 'kind' | 'counts'>): string {
  const c = t.counts
  const parts: string[] = []
  if (c.DOWNLOADING) parts.push(`${c.DOWNLOADING} downloading`)
  if (c.WANTED) parts.push(`${c.WANTED} waiting for a release`)
  if (c.DONE) parts.push(t.kind === 'movie' ? 'in your library' : `${c.DONE} done`)
  if (c.PAUSED) parts.push(`${c.PAUSED} paused`)
  if (c.FAILED) parts.push(`${c.FAILED} failed`)
  if (c.SKIPPED) parts.push(`${c.SKIPPED} skipped`)
  return parts.join(' · ') || (t.kind === 'movie' ? 'Not released for long enough yet' : 'Nothing missing')
}

export const percent = (got: number, total: number) => (total > 0 ? Math.min(100, Math.floor((100 * got) / total)) : null)
