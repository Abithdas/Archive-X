import type { Entity } from '@/api/types'

export type GenderFilter = 'all' | 'female' | 'male' | 'trans'

/** Providers and older data say 'Female', 'transgender_female', 'Trans woman'...; the app groups them as female, male or trans. */
export const normalizeGender = (g?: string | null): '' | 'female' | 'male' | 'trans' => {
  const t = String(g ?? '').trim().toLowerCase()
  return t.includes('trans') ? 'trans' : t === 'f' || t === 'woman' ? 'female' : t === 'm' || t === 'man' ? 'male' : t === 'female' || t === 'male' ? t : ''
}

const fold = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
/** Forgiving name search: ignores case and accents, and every typed word only has to appear somewhere in the name, in any order
 *  ("stone ava" and "ava st" both find "Ava Stone"; "avastone" does too). An empty query matches everything. */
export function nameMatches(name: string, query: string): boolean {
  const q = fold(query.trim())
  if (!q) return true
  const n = fold(name)
  if (n.includes(q) || n.replace(/[\s\-_.']/g, '').includes(q.replace(/[\s\-_.']/g, ''))) return true
  return q.split(/[\s,]+/).filter(Boolean).every((w) => n.includes(w))
}

/** Search by name (case and accent insensitive), optional gender filter, most videos first, then A-Z. */
export function filterEntities(items: Entity[], query: string, gender: GenderFilter = 'all'): Entity[] {
  return items
    .filter((e) => nameMatches(e.name, query) && (gender === 'all' || normalizeGender(e.gender) === gender))
    .sort((a, b) => b.video_count - a.video_count || a.name.localeCompare(b.name))
}

export const pageCount = (total: number, per: number) => Math.max(1, Math.ceil(total / per))
