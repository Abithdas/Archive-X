/** Earlier automatic filename matches waiting for a decision (Keep, or Undo = take the title, performers and studio back off the video). */
export interface AutoMatch { video_id: number; channel_id: string | number; message_id: number; filename?: string; applied_title?: string; applied_studio?: string; applied_performers?: string[]; reason?: string }
export interface AutoMatchList { count?: number; items?: AutoMatch[] }
export type Action = 'keep' | 'undo'
export const BULK_MAX = 100
export const NOTE = "Before V29.4.132 a filename search was applied automatically when only a performer's name matched. These videos got that kind of match. Keep confirms it; Undo takes the title, performers and studio off the video and keeps the match as a suggestion. Nothing changes until you choose."
export const EMPTY = 'Nothing left to review.'
export const attentionText = (n: number) => `${n.toLocaleString()} earlier automatic match${n === 1 ? '' : 'es'} to review — open`
export const appliedLine = (it: Pick<AutoMatch, 'applied_title' | 'applied_studio' | 'applied_performers'>) => `Applied: ${it.applied_title || '—'}${it.applied_studio ? ` · ${it.applied_studio}` : ''}${it.applied_performers?.length ? ` · ${it.applied_performers.join(', ')}` : ''}`
export const firstBatch = (items: readonly AutoMatch[]) => items.slice(0, BULK_MAX).map((i) => i.video_id)
export const allFirstSelected = (items: readonly AutoMatch[], picked: ReadonlySet<number>) => items.length > 0 && firstBatch(items).every((id) => picked.has(id))
export const selectedLabel = (n: number) => `${n} selected`
export const FIRST_100_NOTE = 'Selected the first 100 items. Review bulk actions are limited to 100 at a time.'
export const NEED_SELECTION = 'Select at least one video first.'
export const TOO_MANY = 'Review no more than 100 videos per bulk action.'
export const oneDone = (a: Action) => (a === 'keep' ? 'Match kept.' : 'Match removed; it is now a suggestion.')
export const ONE_FAILED = 'Could not update this video'
export const BULK_FAILED = 'Bulk review failed.'
export const bulkDone = (a: Action, r: { processed?: number; skipped?: number; failed?: number }) => `Bulk ${a}: ${Number(r.processed || 0)} updated, ${Number(r.skipped || 0)} skipped, ${Number(r.failed || 0)} failed.`
export const oneBody = (a: Action) => ({ action: a })
export const bulkBody = (a: Action, ids: readonly number[]) => ({ action: a, video_ids: [...ids] })
