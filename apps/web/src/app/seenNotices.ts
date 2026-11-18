/** Which notices the person has already looked at, so the bell's count is what is NEW, not everything on the list. Kept in this browser. */
import type { Notice } from './notifications'

const KEY = 'ax_seen_notices'
const KEEP = 300

export function loadSeen(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string').slice(-KEEP) : []
  } catch { return [] }
}
export function saveSeen(keys: readonly string[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(keys.slice(-KEEP))) } catch { /* private mode: the count just resets on reload */ }
}

/** The notices not seen yet. */
export function unseen<T extends Pick<Notice, 'key'>>(rows: readonly T[], seen: readonly string[]): T[] {
  const known = new Set(seen)
  return rows.filter((r) => !known.has(r.key))
}

/** `seen` plus every key of `rows` (same array when nothing is new, so a store does not re-render for nothing). */
export function markSeen(seen: readonly string[], rows: readonly Pick<Notice, 'key'>[]): readonly string[] {
  const known = new Set(seen)
  const fresh = rows.map((r) => r.key).filter((k) => !known.has(k))
  return fresh.length ? [...seen, ...fresh].slice(-KEEP) : seen
}
