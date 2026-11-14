/** Scene Match Review: ranked provider candidates for videos, and videos that still need a performer or studio. Nothing changes until a candidate is approved or rejected. */
export interface Suggestion {
  suggestion_id: number; score?: number; channel_id?: string | number; channel_title?: string; message_id?: number; filename?: string; local_title?: string; candidate_title?: string
  performers?: string[]; release_date?: string; missing_fields?: string[]; signals?: string; reason?: string; provider?: string; provider_image?: string; provider_url?: string
  provider_site?: string; studio?: string; provider_site_url?: string; provider_record_id?: string; website_variant_conflict?: boolean
}
export interface Unresolved { media_id: number; channel_id?: string | number; channel_title?: string; message_id?: number; filename?: string; title?: string; duration?: number; studio_name?: string; performers?: string[]; missing_fields?: string[] }
export interface ReviewPage {
  items?: Suggestion[]; count?: number; has_more?: boolean; offset?: number
  unresolved_items?: Unresolved[]; unresolved_count?: number; unresolved_has_more?: boolean; unresolved_offset?: number
  unresolved_channels?: Array<{ channel_id: string | number; title?: string }>; detail?: string
}
const PAGE_SIZE = 24
export const MAX_SELECTION = 500
export const DEFAULT_THRESHOLD = 95

export interface Filters { channel: string; field: 'all' | 'performer' | 'studio' }
export const NO_FILTERS: Filters = { channel: '', field: 'all' }
export const isFiltered = (f: Filters) => !!f.channel || f.field !== 'all'
export const reviewUrl = (f: Filters, so: number, uo: number) => `/api/metadata/scene-match-review?${new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(so), unresolved_limit: String(PAGE_SIZE), unresolved_offset: String(uo), unresolved_channel_id: f.channel, unresolved_missing_field: f.field })}`
export const clamp100 = (v: unknown) => Math.max(0, Math.min(100, Number(v) || 0))
export const thresholdOf = (v: unknown) => Math.max(0, Math.min(100, Number(v ?? DEFAULT_THRESHOLD)))
export const countText = (s: number, u: number, filtered: boolean) => `${s.toLocaleString()} suggestion${s === 1 ? '' : 's'} · ${u.toLocaleString()} need metadata${filtered ? ' · filtered' : ''}`
export const visibleText = (shown: number, total: number) => `${shown.toLocaleString()} of ${total.toLocaleString()}`
export const needs = (fields: readonly string[] | undefined) => (fields ?? []).map((f) => (f === 'performer' ? 'performer' : 'studio'))
export const missingLabel = (fields: readonly string[] | undefined) => (needs(fields).length ? needs(fields).join(' · ') : 'scene identity')

/** Joins the pages loaded so far into one list per kind, without repeating an item a later page also returned. */
export function merge(pages: readonly ReviewPage[]): { suggestions: Suggestion[]; unresolved: Unresolved[]; total: number; unresolvedTotal: number; hasMore: boolean; channels: Array<{ channel_id: string | number; title?: string }> } {
  const sid = new Set<number>(), mid = new Set<number>()
  const suggestions: Suggestion[] = [], unresolved: Unresolved[] = []
  for (const p of pages) {
    for (const s of p.items ?? []) if (!sid.has(s.suggestion_id)) { sid.add(s.suggestion_id); suggestions.push(s) }
    for (const u of p.unresolved_items ?? []) if (!mid.has(u.media_id)) { mid.add(u.media_id); unresolved.push(u) }
  }
  const last = pages[pages.length - 1]
  return { suggestions, unresolved, total: Number(last?.count) || 0, unresolvedTotal: Number(last?.unresolved_count) || 0, hasMore: Boolean(last?.has_more || last?.unresolved_has_more), channels: pages[0]?.unresolved_channels ?? [] }
}
/** Offsets for the next page: how many of each kind were already returned. */
export const nextOffsets = (pages: readonly ReviewPage[]) => ({ so: pages.reduce((n, p) => n + (p.items?.length ?? 0), 0), uo: pages.reduce((n, p) => n + (p.unresolved_items?.length ?? 0), 0) })

/** The redundant "reason" is shown only when it adds something beyond the match signals. */
export function distinctReason(signals: string | undefined, reason: string | undefined): boolean {
  const norm = (t: string | undefined) => String(t ?? '').toLowerCase().replace(/\s+/g, ' ').trim()
  const s = norm(signals), r = norm(reason)
  return !!r && r !== s && !s.includes(r)
}
export const safeHttp = (u: unknown) => (/^https?:\/\//i.test(String(u ?? '')) ? String(u) : '')
export const providerBadge = (s: Pick<Suggestion, 'provider'>) => String(s.provider ?? '').trim()

export const approveUrl = (id: number) => `/api/suggestions/${id}/confirm`
export const rejectUrl = (id: number) => `/api/suggestions/${id}/reject`
export const oneDone = (a: 'approve' | 'reject') => (a === 'approve' ? 'Scene match approved.' : 'Scene suggestion rejected.')
export const oneFailed = (a: 'approve' | 'reject') => `Could not ${a} this suggestion.`
export const selectLimit = `Select up to ${MAX_SELECTION} suggestions at once.`
export const rejectedToast = (r: { rejected?: number; not_pending?: number; failed?: number }) => `Rejected ${Number(r.rejected || 0)} selected suggestion(s); ${Number(r.not_pending || 0)} were no longer pending; ${Number(r.failed || 0)} failed.`
export const approveAboveCopy = (t: number) => ({ title: 'Approve high-confidence scenes', ok: 'Approve candidates', text: `Approve all pending candidates scored ${t.toFixed(1)}% or higher? Performer conflicts and safety checks will still block approval.` })
export function approvedToast(r: Record<string, unknown>): string {
  const n = (k: string) => Number(r[k] || 0)
  return `Approved ${n('approved')}; ${n('ambiguous_skipped')} ambiguous video(s) left for review; ${n('website_variants_skipped')} cross-site variant(s) need individual review; ${n('performer_conflicts')} blocked by performer conflicts.`
}
export const retryToast = (r: { count?: number; id?: unknown; message?: string }) => (r.id ? `Retry queued for ${Number(r.count || 0).toLocaleString()} video(s).` : r.message || 'No unmatched videos found.')
export const retryStatus = (r: { count?: number; message?: string }) => r.message || `Queued ${Number(r.count || 0).toLocaleString()} unmatched/suggested video(s) for one manual recheck.`
/** A video-card-shaped object for the quick editor, from a catalogue entry that needs metadata. */
export const editItem = (u: Unresolved) => ({ id: Number(u.message_id), _cid: String(u.channel_id ?? ''), media_id: u.media_id, name: u.title || u.filename || 'Video', filename: u.filename || '', size: null, duration: Number(u.duration) || 0, width: null, height: null, resolution_category: null, thumbnail_id: Number(u.message_id), favorite: 0 as const, upload_date: null, channel_title: u.channel_title ?? null, performers: u.performers ?? [], studios: u.studio_name ? [u.studio_name] : [], tags: [], studio: u.studio_name || '' })
