/** Bulk edit rules for picked videos: request bodies and the classic page's wording. */
import type { Picked } from './selection'

export type BulkField = 'title' | 'performer' | 'site' | 'performer_site' | 'all'
export type BulkAction = 'move' | 'unknown'

export const NONE_PICKED = 'Select at least one video.'
export const PICK_CHANNEL_FIRST = 'Pick a channel first, then choose the videos to edit.'
export const ENTER_HINT = 'Pick videos, then choose what to change.'
export const countLabel = (n: number) => `${n} selected`
export const videos = (n: number) => `${n} video${n === 1 ? '' : 's'}`

/** Applies a value (or Unknown) to every picked video. Always locked so provider syncs do not undo it. */
export const bulkBody = (picked: Picked[], field: BulkField, action: BulkAction, target = '') => ({ videos: picked, field, target_name: action === 'unknown' ? '' : target.trim(), action, lock: true })
export const updatedMessage = (changed: number | undefined, fallback: number) => `${Number(changed ?? fallback) || 0} video(s) updated and locked.`

export const titleProblem = (t: string) => (t.trim() ? '' : 'Enter the corrected video title.')

const FIELD_NAME: Record<BulkField, string> = { performer: 'performer', site: 'studio', title: 'title', performer_site: 'performer and studio', all: 'performer, studio and title' }
/** Clearing is not undoable from this screen, so (unlike the classic page) it asks first. */
export const unknownConfirm = (field: BulkField, n: number) =>
  `Set the ${FIELD_NAME[field]} of ${videos(n)} to Unknown? The field is also locked so metadata syncs will not refill it. Use Unlock to allow syncs again.`
export const unknownOk = (field: BulkField) => (field === 'all' ? 'Move all to Unknown' : `Clear ${FIELD_NAME[field]}`)

export const recheckMessage = (r: { ok?: boolean; count?: number; detail?: string }, fallback: number) => (r.ok ? `Metadata recheck queued for ${r.count || fallback} video(s).` : r.detail || 'Recheck failed')
export const unlockBody = (picked: Picked[]) => ({ videos: picked, field: 'all' })
export const UNLOCKED = 'Mapping locks removed.'
