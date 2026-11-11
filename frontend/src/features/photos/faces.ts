/** Pure logic for the Photos "Image Analysis" tab (face groups). Mirrors the classic rules and wording. */
import type { FaceFilter } from './photos'

export interface Suggestion { id: number; name?: string; image_url?: string; reason?: string; basis?: string; similarity_score?: number | null }
export interface FaceGroup {
  id: number; label?: string; performer_id?: number | null; performer_name?: string; photo_count?: number; face_count?: number
  cluster_ids?: number[]; detection_ids?: number[]; channel_ids?: string[]; representative_url?: string; performer_image_url?: string; updated_at?: string
  metadata_suggestions?: Suggestion[]; confirmed_assignment_suggestions?: Suggestion[]
}
export interface Analysis { total_photos?: number; analyzed_photos?: number; failed_photos?: number }
export interface FaceGroupsResult { ok?: boolean; detail?: string; groups?: FaceGroup[]; analysis?: Analysis }
export type Source = 'metadata' | 'confirmed_assignment'
export type Tone = '' | 'success' | 'error'

export const LOAD_FAILED = 'Could not load face groups.'
export const EMPTY_TITLE = 'No face groups yet'
export const EMPTY_BODY = 'Archive X groups similar faces from indexed image pixels, not filenames. Run Image scan in Metadata & Assets Manager first; groups stay anonymous until you confirm an assignment.'
export const INTRO = 'Face groups are built from image pixels. Suggestions can use exact source-text matches or similarity to groups you previously confirmed. Every new performer assignment requires your confirmation.'
export const UNANALYZED = 'Face analysis has not been run yet. Run Image scan in Metadata & Assets Manager first so channel images are indexed.'
export const NEEDS_PERFORMER = 'Search and select a local performer before saving this assignment.'
export const JOB_END = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'INTERRUPTED']

export const groupLabel = (g: Pick<FaceGroup, 'id' | 'label' | 'performer_name'>) => String(g.performer_name || g.label || `Face group ${g.id}`)
export const clusterIds = (g: Pick<FaceGroup, 'id' | 'cluster_ids'>) => (g.cluster_ids?.length ? g.cluster_ids : [g.id]).map(Number).filter((n) => Number.isInteger(n) && n > 0)
export const isAssigned = (g: Pick<FaceGroup, 'performer_id'>) => Boolean(g.performer_id)
export const assignedText = (g: FaceGroup) => (isAssigned(g) ? `Assigned · ${g.performer_name || 'Performer'}` : 'Needs performer review')
export const countsLine = (g: FaceGroup) => `${Number(g.face_count || 0).toLocaleString()} face detections · ${Number(g.photo_count || 0).toLocaleString()} images`
export const photosLabel = (g: FaceGroup) => `${Number(g.photo_count || 0).toLocaleString()} photos`

export interface Status { text: string; tone: Tone; analyzeLabel: string; analyzeDisabled: boolean }
export function analysisStatus(groups: readonly FaceGroup[], a: Analysis, selectedCount: number, watching: boolean): Status {
  const total = Number(a.total_photos || 0), done = Number(a.analyzed_photos || 0), failed = Number(a.failed_photos || 0)
  const scope = selectedCount ? 'selected channels' : 'the full indexed library'
  let text = `${done.toLocaleString()} / ${total.toLocaleString()} photos analyzed in ${scope} · ${groups.length.toLocaleString()} visual groups${failed ? ` · ${failed.toLocaleString()} failed (can retry)` : ''}. Groups are based on image pixels, not filenames.`
  let tone: Tone = failed ? 'error' : done ? 'success' : ''
  if (!groups.length) {
    if (total === 0) { text = 'No indexed photos yet. Run Image scan in Metadata & Assets Manager → Processing Stages → Advanced options first.'; tone = '' }
    else if (done >= total && failed === 0) { text = 'Analysis finished, but no faces were detected in this selection.' }
  }
  const complete = total > 0 && done >= total && failed === 0
  return { text, tone, analyzeLabel: watching ? 'Analyzing images…' : complete ? 'All images analyzed' : failed ? 'Retry failed images' : 'Analyze images for faces', analyzeDisabled: watching || total === 0 || complete }
}

export const analyzeCopy = (channels: number) => {
  const scope = channels ? `${channels} selected channel${channels === 1 ? '' : 's'}` : 'the entire indexed Photos library'
  return { title: 'Analyze photos for faces?', ok: 'Analyze images', text: `Analyze image pixels in ${scope} to detect faces and cluster visually similar faces?\n\nThis is processed locally. Archive X will create anonymous face groups and will not automatically assign real performer names. You can assign each group manually after reviewing it. First run downloads the local OpenCV models (about 38 MB).` }
}
export const analyzeBody = (channels: readonly string[]) => ({ channel_ids: [...channels] })
export const jobStatusText = (status: string, progress: unknown, current: unknown) => `Image-based face analysis ${String(status).toLowerCase().replaceAll('_', ' ')} · ${Number(progress || 0).toFixed(0)}%${String(current ?? '').trim() ? ` · ${String(current).trim()}` : ''}`
export const jobDone = (status: string) => JOB_END.includes(String(status).toUpperCase())
export const jobEndText = (status: string) => (status === 'COMPLETED' || status === 'COMPLETED_WITH_ERRORS' ? 'Face analysis complete. Refreshing visual groups…' : `Face analysis ${status.toLowerCase().replaceAll('_', ' ')}. Check Operations for details; failed images can be retried.`)

export const mergeCopy = (n: number) => ({ title: 'Merge face clusters?', ok: 'Merge clusters', text: `Merge ${n} selected visual clusters? Their detected faces and performer assignment will be combined. This can be corrected later by separating individual face detections.` })
export const mergeBody = (ids: readonly number[]) => ({ group_ids: ids.filter((n) => Number.isInteger(n) && n > 0) })
export const mergedText = (n: number, target: unknown) => `Merged ${n} visual clusters into group ${target}.`
export const MERGE_NEEDS_TWO = 'Select at least two face clusters to merge.'
export const splitCopy = { title: 'Separate face detection?', ok: 'Separate face', text: 'Separate this representative face detection into a new anonymous cluster? The other photos in the current cluster will remain unchanged.' }
export const splitText = (newId: unknown) => `Face detection moved to new cluster ${newId}.`
export const firstDetection = (g: FaceGroup) => (g.detection_ids?.length ? Number(g.detection_ids[0]) : 0)

export const assignBody = (g: FaceGroup, performerId: number | null) => ({ performer_id: performerId, group_ids: clusterIds(g) })
export const assignedStatus = (name: string) => `Assigned ${name} to this face group.`
export const CLEARED = 'Performer assignment cleared.'
export const suggestionBody = (g: FaceGroup, s: Suggestion, source: Source, basis: string) => ({
  performer_id: s.id, group_ids: clusterIds(g), suggestion_source: source,
  suggestion_score: s.similarity_score == null || !Number.isFinite(Number(s.similarity_score)) ? null : Number(s.similarity_score),
  suggestion_reason: source === 'confirmed_assignment' ? 'Confirmed from a previously assigned visual group' : `Confirmed from source metadata: ${basis || 'text match'}`,
})
export const confirmedText = (name: string) => `Confirmed ${name} for this face group. Future suggestions can learn from this confirmation.`
export const rejectBody = (g: FaceGroup, s: Suggestion, source: Source) => ({ group_ids: clusterIds(g), performer_id: s.id, source, similarity_score: s.similarity_score == null || !Number.isFinite(Number(s.similarity_score)) ? null : Number(s.similarity_score), reason: 'Rejected by user in Image Analysis' })
export const REJECTED = 'Suggestion rejected and remembered; it will stay hidden on future refreshes.'
export const scoreText = (s: Suggestion) => (s.similarity_score != null && Number.isFinite(Number(s.similarity_score)) ? `Embedding similarity ${Number(s.similarity_score).toFixed(2)} · not a probability` : '')
export const suggestionReason = (s: Suggestion, source: Source) => String(s.reason || (source === 'metadata' ? 'Source text match' : 'Similar to a previously confirmed group'))
export const suggestionsFor = (g: FaceGroup) => (isAssigned(g) ? { confirmed: [], metadata: [] } : { confirmed: (g.confirmed_assignment_suggestions ?? []).slice(0, 3), metadata: (g.metadata_suggestions ?? []).slice(0, 3) })

/** Selecting a group to view photos: an assigned group filters by performer, others by their cluster ids. */
export function viewFilter(g: FaceGroup): { filter: FaceFilter; channels: string[] } {
  const performerId = g.performer_id ? Number(g.performer_id) : null
  return { filter: { performerId, groupId: Number(g.id), groupIds: performerId ? [] : clusterIds(g), label: groupLabel(g) }, channels: (g.channel_ids ?? []).map(String) }
}
export const filterLabel = (f: FaceFilter) => `Face group · ${f.label}`
export const differentCopy = "Remove this photo's face detection(s) from the current group?\n\nArchive X will move those detections into new unassigned group(s). The photo and its Telegram message will not be deleted."
export function differentBody(photoId: number, f: FaceFilter, groups: readonly FaceGroup[]) {
  const active = groups.find((g) => Number(g.id) === Number(f.groupId))
  const ids = (active?.cluster_ids?.length ? active.cluster_ids : f.groupIds.length ? f.groupIds : f.groupId && f.groupId > 0 ? [f.groupId] : []).map(Number).filter((n) => Number.isInteger(n) && n > 0)
  return photoId && ids.length ? { photo_id: photoId, group_ids: ids } : null
}
export const DIFFERENT_NEEDS_GROUP = 'Open a face-group gallery first, then use Different person on the incorrect photo.'
