import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

/** How many unnamed faces look like a named person (and how many of those strongly): the "new matches to check" for that person. */
export interface ReviewCount { suggestions: number; strong: number }

const asCount = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0)

/** Per person (by performer id). Face learning exists only in the desktop app; anywhere else the answer is "nobody", not an error. */
export function parseReview(body: unknown): Record<number, ReviewCount> {
  const out: Record<number, ReviewCount> = {}
  const people = (body as { people?: unknown } | null)?.people
  if (!Array.isArray(people)) return out
  for (const p of people as Array<Record<string, unknown>>) {
    const id = Number(p?.performer_id)
    const n = asCount(p?.suggestions)
    if (Number.isInteger(id) && id > 0 && n > 0) out[id] = { suggestions: n, strong: asCount(p?.strong) }
  }
  return out
}

export const newMatchesText = (n: number) => `${n.toLocaleString()} new match${n === 1 ? '' : 'es'} to check`

export const useReviewCounts = (enabled = true) =>
  useQuery({
    queryKey: ['face-learning', 'review'] as const,
    enabled,
    staleTime: 30_000,
    retry: false,
    queryFn: async () => { try { return parseReview(await api('/api/photos/faces/learning/review')) } catch { return {} as Record<number, ReviewCount> } },
  })
