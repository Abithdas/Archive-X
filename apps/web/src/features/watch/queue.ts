import type { LibraryItem } from '@/api/types'

/** How the videos that follow the open one are chosen (the same names as Settings, Experience, "Autoplay source"). */
export type QueueMode = 'context' | 'collection' | 'performer' | 'studio' | 'recommended'
export const QUEUE_MODES: ReadonlyArray<readonly [QueueMode, string]> = [['context', 'Current queue'], ['collection', 'Same channel'], ['performer', 'Same performer'], ['studio', 'Same studio'], ['recommended', 'Recommended']]
export const isQueueMode = (v: unknown): v is QueueMode => QUEUE_MODES.some(([k]) => k === v)

const same = (a: LibraryItem, b: LibraryItem) => a.id === b.id && a._cid === b._cid
const key = (i: LibraryItem) => `${i._cid}:${i.id}`

/** What plays after `current` in a list: the ones that follow it, then (like autoplay's wrap-around) the ones before it. A list that does not hold it is all there is. */
export function after(items: readonly LibraryItem[], current: LibraryItem): LibraryItem[] {
  const at = items.findIndex((i) => same(i, current))
  return at < 0 ? items.filter((i) => !same(i, current)) : [...items.slice(at + 1), ...items.slice(0, at)]
}

const norm = (v: string) => v.trim().toLowerCase()
const UNKNOWN = new Set(['unknown performer', 'unknown studio'])

/** Other videos, the most alike first: shared performers count most, then the same studio, then shared tags. Ties keep the order they came in. */
export function recommended(pool: readonly LibraryItem[], current: LibraryItem): LibraryItem[] {
  const people = new Set((current.performers ?? []).map(norm).filter((n) => n && !UNKNOWN.has(n)))
  const studios = new Set([current.studio, ...(current.studios ?? [])].map((s) => norm(String(s ?? ''))).filter((n) => n && !UNKNOWN.has(n)))
  const tags = new Set((current.tags ?? []).map(norm).filter(Boolean))
  const seen = new Set<string>([key(current)])
  const scored: Array<{ item: LibraryItem; score: number; at: number }> = []
  pool.forEach((item, at) => {
    if (seen.has(key(item))) return
    seen.add(key(item))
    const shared = (list: readonly string[] | undefined, set: Set<string>) => (list ?? []).filter((n) => set.has(norm(n))).length
    const score = 3 * shared(item.performers, people) + 2 * ([item.studio, ...(item.studios ?? [])].some((s) => studios.has(norm(String(s ?? '')))) ? 1 : 0) + 0.5 * shared(item.tags, tags)
    scored.push({ item, score, at })
  })
  return scored.sort((a, b) => b.score - a.score || a.at - b.at).map((s) => s.item)
}

export interface Sources { context: readonly LibraryItem[]; collection: readonly LibraryItem[]; performer: readonly LibraryItem[]; studio: readonly LibraryItem[] }

/** The videos that follow `current` under a mode, and the one before it (for the Previous button). A mode with nothing to offer falls back to the channel's own order, so there is always something next. */
export function queueFor(mode: QueueMode, current: LibraryItem, s: Sources): { next: LibraryItem[]; previous: LibraryItem | undefined; source: QueueMode } {
  const base = (m: QueueMode): readonly LibraryItem[] => (m === 'performer' ? s.performer : m === 'studio' ? s.studio : m === 'collection' ? s.collection : s.context)
  const holds = (list: readonly LibraryItem[]) => list.some((i) => same(i, current))
  const usable = (m: QueueMode) => (m === 'recommended' ? [...s.context, ...s.collection, ...s.performer, ...s.studio].some((i) => !same(i, current)) : base(m).some((i) => !same(i, current)))
  const source: QueueMode = usable(mode) ? mode : usable('collection') ? 'collection' : 'context'
  if (source === 'recommended') {
    const next = recommended([...s.performer, ...s.studio, ...s.context, ...s.collection], current)
    return { next, previous: undefined, source }
  }
  let list = base(source)
  if (!holds(list) && source === 'context') list = holds(s.collection) ? s.collection : list
  const at = list.findIndex((i) => same(i, current))
  return { next: after(list, current), previous: at > 0 ? list[at - 1] : undefined, source }
}
