type Obj = Record<string, unknown>
const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const s = (v: unknown) => (typeof v === 'string' ? v : '')

/** "2026-11-26T08:30:00" -> "26 Nov 2026"; nothing yet -> "never". */
export function whenText(iso: string): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? 'never' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export type DatabaseRow = { label: string; value: string }

/** What the Database card lists: the size, the unused space inside the file, and when it was last looked after. */
export function databaseRows(r: Obj, fmt: (b: number) => string): DatabaseRow[] {
  const free = n(r.free_bytes)
  return [
    { label: 'Database size', value: fmt(n(r.file_bytes)) },
    { label: 'Unused space inside it', value: `${fmt(free)} (${n(r.free_percent)}%)` },
    { label: 'Gives space back by itself', value: r.incremental ? 'Yes, while the app is idle' : 'Not yet: compact it once to switch this on' },
    { label: 'Last compacted', value: whenText(s(r.last_compact)) },
    { label: 'Search statistics refreshed', value: whenText(s(r.last_optimize)) },
  ]
}

/** The advice under the rows: why compacting is worth it now, or why it cannot run. */
export function databaseNote(r: Obj, fmt: (b: number) => string): string {
  if (r.can_compact === false) return `Compacting needs about ${fmt(n(r.compact_needs_bytes))} of free disk space and ${fmt(n(r.disk_free_bytes))} is free.`
  if (r.compact_recommended) return 'Compacting would give back unused space and keep the database from growing. It takes a few seconds.'
  return 'Nothing to do now. The app gives unused space back by itself while it is idle.'
}

export const compactConfirmText = (r: Obj, fmt: (b: number) => string) =>
  `Compacting rewrites the database file. Writing pauses for a few seconds (longer on a large library or a slow disk) and it needs about ${fmt(n(r.compact_needs_bytes))} of free disk space while it runs.\n\nKeep the app open until it finishes.`

export function compactResultMessage(r: Obj, fmt: (b: number) => string): string {
  const saved = n(r.saved_bytes)
  return saved > 0 ? `Compacted in ${n(r.seconds)} s · ${fmt(saved)} given back` : `Compacted in ${n(r.seconds)} s · the database was already compact`
}
