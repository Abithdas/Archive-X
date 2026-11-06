import type { Entity } from '@/api/types'

export type GenderFilter = 'all' | 'female' | 'male'

/** Search by name (case and accent insensitive), optional gender filter, most videos first, then A-Z. */
export function filterEntities(items: Entity[], query: string, gender: GenderFilter = 'all'): Entity[] {
  const fold = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
  const q = fold(query.trim())
  return items
    .filter((e) => (!q || fold(e.name).includes(q)) && (gender === 'all' || (e.gender ?? '').toLowerCase() === gender))
    .sort((a, b) => b.video_count - a.video_count || a.name.localeCompare(b.name))
}

export const pageCount = (total: number, per: number) => Math.max(1, Math.ceil(total / per))
