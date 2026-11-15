import type { Entity, EntityKind } from '@/api/types'
import { nameMatches } from '@/features/entities/filter'

export interface Suggestion {
  /** `performers:12`: unique across both lists. */
  id: string
  kind: EntityKind
  entityId: number
  title: string
  description: string
  /** The picture, like a site's favicon in a search list; the tile falls back to the first letter when it is missing or broken. */
  image: string
}

const plural = (n: number, one: string) => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`
const imageFor = (kind: EntityKind, e: Entity) => `/api/${kind}/${e.id}/image?w=96&v=${encodeURIComponent(e.image ?? '')}`

const norm = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase().trim()

/**
 * Performers and studios whose name matches what is typed (the same forgiving match the lists use), names that start with it first, then
 * the ones with the most videos. At most `limit`; nothing for an empty query.
 */
export function suggest(performers: readonly Entity[], studios: readonly Entity[], query: string, limit = 8): Suggestion[] {
  const q = norm(query)
  if (!q) return []
  const make = (kind: EntityKind, list: readonly Entity[]) =>
    list
      .filter((e) => nameMatches(e.name, query))
      .map((e) => ({ e, kind, starts: norm(e.name).startsWith(q) ? 0 : 1 }))
  return [...make('performers', performers), ...make('studios', studios)]
    .sort((a, b) => a.starts - b.starts || b.e.video_count - a.e.video_count || a.e.name.localeCompare(b.e.name))
    .slice(0, Math.max(0, limit))
    .map(({ e, kind }) => ({
      id: `${kind}:${e.id}`,
      kind,
      entityId: e.id,
      title: e.name,
      description: `${kind === 'performers' ? 'Performer' : 'Studio'} · ${plural(e.video_count, 'video')}`,
      image: imageFor(kind, e),
    }))
}

/** Where picking a suggestion goes. */
export const suggestionPath = (s: Pick<Suggestion, 'kind' | 'entityId'>) => `/${s.kind}/${s.entityId}`

export interface Part { text: string; match: boolean }

/** Split `text` around the first place the query appears, ignoring case and accents, so the matching letters can be marked. */
export function highlightParts(text: string, query: string): Part[] {
  const q = norm(query)
  if (!q) return [{ text, match: false }]
  const folded = norm(text)
  // Folding can change the length (a letter and its accent become one); then positions no longer line up, so show the text plain.
  const at = folded.length === text.trim().length || folded.length === text.length ? folded.indexOf(q) : -1
  if (at < 0) return [{ text, match: false }]
  const offset = folded.length === text.length ? 0 : text.length - text.trimStart().length
  const from = at + offset
  const parts: Part[] = [
    { text: text.slice(0, from), match: false },
    { text: text.slice(from, from + q.length), match: true },
    { text: text.slice(from + q.length), match: false },
  ]
  return parts.filter((p) => p.text)
}
