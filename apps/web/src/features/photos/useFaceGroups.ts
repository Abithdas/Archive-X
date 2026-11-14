import { useQuery } from '@tanstack/react-query'
import { ApiError, api } from '@/api/client'
import { LOAD_FAILED, type FaceGroupsResult } from './faces'

/** The groups address. `suggestions` false leaves out the work of guessing who each group might be (seconds on a big library); the Photos page only needs the people themselves. */
export const faceGroupsUrl = (selected: readonly string[], suggestions = true, top = 0) => {
  const p = new URLSearchParams(); if (selected.length) p.set('channels', selected.join(',')); if (!suggestions) p.set('suggestions', '0'); if (top) p.set('top', String(top))
  const q = p.toString(); return `/api/photos/faces/groups${q ? `?${q}` : ''}`
}
export function useFaceGroups(selected: readonly string[], enabled = true, suggestions = true, top = 0) {
  return useQuery({
    queryKey: ['face-groups', selected, suggestions, top] as const,
    enabled,
    queryFn: async () => { const r = await api<FaceGroupsResult>(faceGroupsUrl(selected, suggestions, top)); if (!r || r.ok !== true) throw new ApiError(0, r?.detail || LOAD_FAILED); return r },
    staleTime: 0,
  })
}
