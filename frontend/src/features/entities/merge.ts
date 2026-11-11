/** Merge duplicate performers: pick one record to keep, then every duplicate to fold into it. Ported from the classic Performers page. */
export interface Named { id: number; name: string }
export interface MergeState { active: boolean; keepId: number | null; mergeIds: number[]; busy: boolean }
export const IDLE: MergeState = { active: false, keepId: null, mergeIds: [], busy: false }

export const ENTER_HINT = 'Select one performer to keep, then select every duplicate to merge into it.'
export const modeLabel = (active: boolean) => (active ? 'Cancel merge' : 'Merge duplicates')
export const commitLabel = (n: number) => `Merge selected (${n})`
export const canCommit = (s: MergeState, list: readonly Named[]) => s.active && !s.busy && s.keepId !== null && s.mergeIds.length > 0 && list.some((p) => p.id === s.keepId)

export function hint(s: MergeState, list: readonly Named[]): string {
  if (!s.active) return ''
  const n = s.mergeIds.length
  const dup = `${n} duplicate${n === 1 ? '' : 's'}`
  if (s.busy) return `Merging ${dup} atomically…`
  const keep = list.find((p) => p.id === s.keepId)
  return keep ? `Keep: ${keep.name} · ${dup} selected. Click cards to add/remove, then merge.` : 'Select the canonical performer to keep first.'
}

/** Clicking a card in merge mode. The first click chooses the keeper; later clicks toggle duplicates. */
export function pick(s: MergeState, id: number, list: readonly Named[]): { state: MergeState; message: string } {
  if (!s.active || s.busy) return { state: s, message: '' }
  const chosen = list.find((p) => p.id === id)
  if (!chosen) return { state: s, message: 'Performer not found in the current list. Reload and try again.' }
  if (s.keepId === null) return { state: { ...s, keepId: id }, message: `Keep “${chosen.name}”. Now select all duplicates to merge into it.` }
  if (s.keepId === id) return { state: s, message: 'This is the keeper. Select other performer cards to add them as duplicates.' }
  if (!list.some((p) => p.id === s.keepId)) return { state: { ...s, keepId: null, mergeIds: [] }, message: 'Keeper record is no longer available. Select it again.' }
  return { state: { ...s, mergeIds: s.mergeIds.includes(id) ? s.mergeIds.filter((x) => x !== id) : [...s.mergeIds, id] }, message: '' }
}

export const toggleMode = (s: MergeState): { state: MergeState; message: string } =>
  s.busy ? { state: s, message: '' } : { state: { active: !s.active, keepId: null, mergeIds: [], busy: false }, message: s.active ? '' : ENTER_HINT }

/** What the confirmation shows: the keeper, up to 8 duplicates by name and id, and the rules of the merge. */
export function confirmation(keep: Named, ids: readonly number[], list: readonly Named[]) {
  const names = ids.map((id) => { const p = list.find((x) => x.id === id); return p ? `${p.name} (ID ${id})` : `#${id}` })
  const preview = names.slice(0, 8).map((n) => `• ${n}`).join('\n') + (names.length > 8 ? `\n• …and ${names.length - 8} more` : '')
  return {
    title: 'Merge duplicate performers',
    ok: `Merge ${ids.length} selected`,
    text: `Merge ${ids.length} duplicate performer record${ids.length === 1 ? '' : 's'} into “${keep.name}”?\n\n${preview}\n\nProvider IDs, aliases, video links and related metadata will be consolidated. All selected merges run in one transaction and are rolled back if any merge fails. This cannot be undone.`,
  }
}
export const mergeBody = (keepId: number, ids: readonly number[]) => ({ keep_id: keepId, merge_ids: [...ids] })
/** The ids to send: never the keeper itself. */
export const duplicatesOf = (s: MergeState) => s.mergeIds.filter((id) => id !== s.keepId)

export const NEED_SELECTION = 'Select a keeper and at least one duplicate.'
export const successMessage = (r: { merged_count?: number; name?: string }, ids: number, keepName: string) => {
  const n = Number(r.merged_count || ids)
  return `Merged ${n} duplicate${n === 1 ? '' : 's'} into “${r.name || keepName}”. Video associations and provider IDs were preserved.`
}
export const FAILED = 'Performer batch merge failed. No changes were committed.'

export type SortKey = 'default' | 'name_asc' | 'name_desc'
export const SORTS: ReadonlyArray<{ value: SortKey; label: string }> = [{ value: 'default', label: 'Default order' }, { value: 'name_asc', label: 'Name A–Z' }, { value: 'name_desc', label: 'Name Z–A' }]
/** Default keeps the directory's usual order (most videos first); the name sorts ignore accents and case. */
export function sortBy<T extends Named>(list: readonly T[], key: SortKey): T[] {
  if (key === 'default') return [...list]
  const dir = key === 'name_asc' ? 1 : -1
  return [...list].sort((a, b) => dir * a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}
