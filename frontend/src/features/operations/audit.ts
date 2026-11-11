/** Duplicate performer audit: read-only review, then explicit merges. Ported from the classic Operations page. */
type Obj = Record<string, unknown>
const s = (v: unknown) => (v == null ? '' : String(v))
const n = (v: unknown) => Number(v) || 0

export interface Member { id: number; name?: string; video_count?: number; has_image?: boolean; provider_ids?: Record<string, unknown>; aliases?: string[] }
export interface Group {
  kind: string; title?: string; reason?: string; recommended_treatment?: string; similarity?: number; contains_unknown_performer?: boolean; auto_merge_eligible?: boolean
  performers?: Member[]; suggested_target?: { id: number; name?: string; video_count?: number } | null
}
export interface Audit {
  ok?: boolean; groups?: Group[]; performer_count?: number; candidate_merge_groups?: number; review_groups?: number; suggestion_groups?: number; junk_fragment_groups?: number
  unmatched_performer_count?: number; suggestions_truncated?: boolean; suggestion_groups_returned?: number; detail?: string
}

export const KINDS: Record<string, { title: string; treatment: string; note: string }> = {
  strong_merge: { title: 'Same canonical name and compatible provider IDs', treatment: 'Eligible for automatic cleanup', note: 'Normalized canonical names match, with no conflicting provider IDs.' },
  candidate_merge: { title: 'Different spelling with a shared alias or provider ID', treatment: 'Depends on evidence', note: 'An identical namespaced provider ID can be auto-merged; alias-only evidence stays in review.' },
  review: { title: 'Conflicting identity evidence', treatment: 'Keep in review', note: 'Different confirmed provider IDs may indicate different people. Do not merge automatically.' },
  suggest_review: { title: 'Similar but not identical names', treatment: 'Suggest for review only', note: 'Name similarity alone is not proof of identity. Keep separate until confirmed.' },
}
export const HEADINGS: Record<string, string> = { strong_merge: 'Strong candidates', candidate_merge: 'Alias / provider candidates', review: 'Review before merging', suggest_review: 'Similar-name suggestions', junk_fragment: 'Likely filename/title fragments' }
export const headingFor = (kind: string) => HEADINGS[kind] ?? 'Candidates'
export const kindInfo = (kind: string) => KINDS[kind] ?? KINDS.review!

export const autoGroups = (a: Audit) => (a.groups ?? []).filter((g) => g.auto_merge_eligible === true)
export const autoLabel = (count: number) => (count ? `Auto-merge high-confidence (${count})` : 'No safe auto-merges')
export function totalsLine(a: Audit): string {
  return `Scanned ${n(a.performer_count).toLocaleString()} performer records · ${autoGroups(a).length} high-confidence groups safe to auto-merge · ${n(a.candidate_merge_groups)} alias/provider candidates · ${n(a.review_groups)} identity-conflict groups · ${n(a.suggestion_groups)} similar-name suggestions · ${n(a.junk_fragment_groups)} likely filename/title fragments · ${n(a.unmatched_performer_count)} records with no linked duplicate evidence`
}
export const truncatedNote = (a: Audit) => (a.suggestions_truncated ? `Showing ${n(a.suggestion_groups_returned)} of ${n(a.suggestion_groups)} similar-name suggestions. The audit scanned the full catalogue; lower-confidence suggestions are capped to keep the review dialog usable.` : '')

export function identityText(p: Member): string {
  const values = Object.entries(p.provider_ids ?? {}).flatMap(([provider, v]) => { const raw = Array.isArray(v) ? v : [v]; return raw.length ? [`${provider}: ${raw.join(', ')}`] : [] })
  return values.length ? values.join(' · ') : 'No saved provider ID'
}
export const memberLine = (p: Member) => `ID ${n(p.id)} · ${n(p.video_count)} video${n(p.video_count) === 1 ? '' : 's'}${p.has_image ? ' · artwork' : ''}`
export const similarityLabel = (g: Group) => (g.similarity ? ` · Similarity ${Math.round(n(g.similarity) * 100)}%` : '')
export const isProtected = (g: Group) => Boolean(g.contains_unknown_performer)
export const groupFooter = (g: Group) => (isProtected(g)
  ? 'Unknown Performer is protected. Keep this group separate; unresolved video identities should stay under Unknown Performer or in Performer Suggestions.'
  : 'For manual merge, choose exactly one “Keep” record and check one or more “Merge” records. Similar-name suggestions should stay separate unless identity is confirmed.')

// ── manual merge of one group ──
export const MAX_MERGE = 500
export function mergeIds(keepId: number, checked: Iterable<number>): number[] { return [...new Set(checked)].filter((id) => id > 0 && id !== keepId) }
export const canMerge = (keepId: number | null, checked: Iterable<number>) => keepId !== null && mergeIds(keepId, checked).length > 0
export function manualConfirm(group: Group, keepId: number, ids: readonly number[]): { ok: true; title: string; text: string; okLabel: string } | { ok: false; message: string } {
  const keep = (group.performers ?? []).find((p) => p.id === keepId)
  if (!keep || !ids.length) return { ok: false, message: 'Choose one keeper and at least one different performer to merge.' }
  if (ids.length > MAX_MERGE) return { ok: false, message: `Merge no more than ${MAX_MERGE} duplicate performers in one operation.` }
  const names = ids.map((id) => (group.performers ?? []).find((p) => p.id === id)?.name || `#${id}`)
  const preview = names.slice(0, 8).map((x) => `• ${x}`).join('\n') + (names.length > 8 ? `\n• …and ${names.length - 8} more` : '')
  return { ok: true, title: 'Confirm performer cleanup', okLabel: `Merge ${ids.length}`, text: `Merge ${ids.length} selected record(s) into “${keep.name}”?\n\n${preview}\n\nThe keeper name will remain canonical. Video links, provider IDs, aliases and related metadata are consolidated. This is a permanent change and all selected merges run atomically.` }
}
export const mergeBody = (keepId: number, ids: readonly number[]) => ({ keep_id: keepId, merge_ids: [...ids] })
export const mergedMessage = (r: { merged_count?: number; name?: string }, ids: number, keepName: string) => `Merged ${n(r.merged_count) || ids} performer record(s) into “${r.name || keepName}”.`
export const MERGE_FAILED = 'The performer cleanup failed; no changes were committed.'

// ── automatic cleanup ──
export function autoConfirm(a: Audit) {
  const eligible = autoGroups(a)
  const rows = eligible.reduce((t, g) => t + Math.max(0, (g.performers ?? []).length - 1), 0)
  return {
    eligible: eligible.length, rows, title: 'Auto-clean high-confidence duplicates', okLabel: `Auto-merge ${rows}`,
    text: `Automatically merge ${rows} duplicate performer record(s) in ${eligible.length} high-confidence group(s)?\n\nArchive X will merge only records with the same normalized canonical name or an identical namespaced provider ID, with no conflicting identity evidence. It will keep alias-only matches, different confirmed provider IDs, similar spellings, and Unknown Performer separate for review. Uncertain video identities remain in Performer Suggestions or Unknown Performer.\n\nAll selected automatic merges run in one transaction and are rolled back together if any merge fails.`,
  }
}
export const NOTHING_SAFE = 'No high-confidence performer duplicates are safe to auto-merge. Ambiguous matches remain separate for review.'
export const AUTO_FAILED = 'Automatic performer cleanup failed; no changes were committed.'
export interface AutoResult { ok?: boolean; audit?: Audit; merged_performer_count?: number; merged_group_count?: number; ambiguous_groups_left_for_review?: number; protected_unknown_group_count?: number; detail?: string }
export const autoOutcome = (r: AutoResult) => `Auto-cleanup complete: merged ${n(r.merged_performer_count)} performer record(s) in ${n(r.merged_group_count)} group(s). Left ${n(r.ambiguous_groups_left_for_review)} ambiguous group(s) separate for review; protected Unknown Performer groups: ${n(r.protected_unknown_group_count)}.`
export const autoToast = (r: AutoResult) => `Auto-cleanup merged ${n(r.merged_performer_count)} high-confidence duplicate performer record(s). Ambiguous matches were left for review.`

// ── filename / title fragments ──
export type JunkAction = 'merge' | 'delete'
export const targetLine = (t: NonNullable<Group['suggested_target']>) => `ID ${n(t.id)} · ${n(t.video_count)} linked videos`
export function junkCheck(group: Group, action: JunkAction): string {
  const p = (group.performers ?? [])[0]
  if (!p) return 'This audit record is stale. Refresh the audit before changing it.'
  if (action === 'merge' && !group.suggested_target) return 'No suggested merge target is available.'
  if (action === 'delete' && n(p.video_count) > 0) return 'This performer still has linked videos; merge them first.'
  return ''
}
export function junkConfirm(group: Group, action: JunkAction) {
  const p = (group.performers ?? [])[0]!
  const target = s(group.suggested_target?.name) || 'the selected target'
  return action === 'merge'
    ? { title: 'Merge filename fragment', okLabel: 'Merge performer', text: `Merge “${p.name}” into “${target}”? All video links, aliases, provider identities and related metadata will be preserved on the target.` }
    : { title: 'Delete unlinked fragment', okLabel: 'Delete record', text: `Delete “${p.name}”? This is allowed only because the record has no linked videos. This cannot be undone.` }
}
export const junkBody = (performerId: number, action: JunkAction, targetId: number | null) => ({ performer_id: performerId, action, target_id: targetId })
export const junkOutcome = (action: JunkAction, name: string, r: { name?: string }, targetName: string) => (action === 'merge' ? `Merged likely fragment “${name}” into “${r.name || targetName}”.` : `Deleted unlinked junk record “${name}”.`)
export const JUNK_FAILED = 'Performer cleanup failed; no records were changed.'
export const AUDIT_FAILED = 'The performer audit failed; no records were changed.'
export const AUDITING = 'Auditing the entire performer catalogue…'
export const AUDIT_NOTE = 'Reading performer identities, aliases, provider IDs and video links. The audit itself is read-only; automatic merges require a separate explicit confirmation.'
export const NO_GROUPS_TITLE = 'No likely duplicate groups found.'
export const NO_GROUPS_NOTE = 'No strong name, provider-ID, alias, or review-only similarity candidates were found. No records were changed.'
export type { Obj }
