/** Telegram Transfer Manager rules, ported from startUnifiedTransfer (classic UI). Pure so every decision is testable. */
export type Source = 'local' | 'telegram'
export const ORIENTATIONS = [['BOTH', 'Preserve / both'], ['HORIZONTAL', 'Horizontal only'], ['VERTICAL', 'Vertical only']] as const
export const RESOLUTIONS = [['ANY', 'Any resolution'], ['480P', 'SD · 480p (720 × 480)'], ['720P', 'HD · 720p (1280 × 720)'], ['1080P', 'Full HD · 1080p (1920 × 1080)'], ['1440P', 'Quad HD · 1440p (2560 × 1440)'], ['2160P', '4K UHD · 2160p (3840 × 2160)'], ['4320P', '8K · 4320p (7680 × 4320)']] as const

export interface LocalOptions {
  folder: string
  destination: string
  images: boolean
  subfolders: boolean
  deleteSource: boolean
  resolution: string
  orientation: string
  oversized: 'optimize' | 'skip'
  /** Only videos shorter than this many minutes; empty = any length. */
  maxMinutes: string
}
export interface TelegramOptions {
  channels: string[]
  destination: string
  sourceMedia: 'VIDEO' | 'FILE'
  destinationMedia: 'VIDEO' | 'FILE'
  orientation: string
  resolution: string
  dupSource: boolean
  dupDestination: boolean
  dupDb: boolean
  cleanup: boolean
  trash: string
  /** Videos with no saved 36-frame snapshot set: skip them (the source stays) or make the set from Telegram first. */
  snapshots: 'existing' | 'generate'
  /** Only videos shorter than this many minutes; empty = any length. */
  maxMinutes: string
}
export const DEFAULT_LOCAL: LocalOptions = { folder: '', destination: '', images: false, subfolders: false, deleteSource: true, resolution: 'ANY', orientation: 'BOTH', oversized: 'optimize', maxMinutes: '' }
export const DEFAULT_TELEGRAM: TelegramOptions = { channels: [], destination: '', sourceMedia: 'VIDEO', destinationMedia: 'VIDEO', orientation: 'BOTH', resolution: 'ANY', dupSource: true, dupDestination: true, dupDb: true, cleanup: false, trash: '', snapshots: 'existing', maxMinutes: '' }

/** The limit as a number of minutes: undefined when empty or 0 (no limit), NaN when it is not a positive number. */
export function parseMaxMinutes(text: string): number | undefined {
  const t = (text ?? '').trim().replace(',', '.')
  if (!t) return undefined
  const n = Number(t)
  if (!Number.isFinite(n) || n < 0 || n > 24 * 60) return Number.NaN
  return n === 0 ? undefined : n
}
export const MAX_MINUTES_PROBLEM = 'Enter the maximum length as minutes above 0 (for example 10), or leave it empty for any length.'
export const describeMaxMinutes = (text: string): string => { const n = parseMaxMinutes(text); return n === undefined || Number.isNaN(n) ? 'Any length' : `Shorter than ${n} min` }

/** First problem that blocks the run, or '' when it can start. Same wording as the classic page. */
export function validateLocal(o: LocalOptions): string {
  if (!o.folder.trim()) return 'Enter a source folder.'
  if (!o.destination) return 'Choose a destination channel.'
  if (!o.images && Number.isNaN(parseMaxMinutes(o.maxMinutes))) return MAX_MINUTES_PROBLEM
  return ''
}
export function validateTelegram(o: TelegramOptions): string {
  if (!o.channels.length) return 'Select at least one source channel.'
  if (!o.destination) return 'Choose a destination channel.'
  if (o.channels.includes(String(o.destination))) return 'Destination cannot be one of the selected source channels.'
  if (o.cleanup && !o.trash) return 'Choose a trash channel when cleanup is enabled.'
  if (Number.isNaN(parseMaxMinutes(o.maxMinutes))) return MAX_MINUTES_PROBLEM
  return ''
}

export function localPayload(o: LocalOptions, includeSubfolders = o.subfolders) {
  const base = { source: 'local', media_type: o.images ? 'PHOTOS' : 'VIDEO', folder: o.folder.trim(), destination_channel: o.destination, include_subfolders: includeSubfolders, delete_local_after_delivery: o.deleteSource }
  const limit = parseMaxMinutes(o.maxMinutes)
  return o.images ? base : { ...base, resolution_filter: o.resolution || 'ANY', orientation: o.orientation || 'BOTH', oversized_file_policy: o.oversized || 'optimize', ...(limit ? { max_duration_minutes: limit } : {}) }
}
export const SNAPSHOT_CHOICES = [
  ['existing', 'Use saved snapshots only (recommended): skip videos that have none, keep them in the source'],
  ['generate', 'Make missing snapshots from Telegram first (slower; reads each video from Telegram)'],
] as const
export const telegramPayload = (o: TelegramOptions) => ({
  source: 'telegram', source_channels: o.channels, destination_channel: o.destination, source_mode: o.sourceMedia, destination_mode: o.destinationMedia,
  orientation: o.orientation || 'BOTH', resolution_filter: o.resolution || 'ANY',
  duplicate_check: { source: o.dupSource, destination: o.dupDestination, database: o.dupDb }, cleanup: { enabled: o.cleanup, trash_channel: o.trash },
  snapshot_policy: o.snapshots || 'existing',
  ...(parseMaxMinutes(o.maxMinutes) ? { max_duration_minutes: parseMaxMinutes(o.maxMinutes) } : {}),
})

export interface PhotoScan { direct_count?: number; nested_count?: number; recursive_count?: number; recursive_file_count?: number; recursive_extensions?: Record<string, number>; detail?: string }
export type PhotoPlan =
  | { kind: 'error'; message: string }
  | { kind: 'ask'; directCount: number; nestedCount: number; message: string }
  | { kind: 'go'; includeSubfolders: boolean; count: number }
/** Decide what to do with a folder scan before queueing an image upload. `ask` needs the user's choice (see resolveAsk). */
export function planPhotoScan(scan: PhotoScan | null | undefined, folder: string, includeSubfolders: boolean): PhotoPlan {
  if (!scan || typeof scan.direct_count !== 'number') return { kind: 'error', message: `Could not scan the selected photo folder: ${scan?.detail || 'folder scan failed. No upload was queued.'}` }
  const direct = Number(scan.direct_count || 0)
  const nested = Number(scan.nested_count || 0)
  if (!includeSubfolders && nested > 0) {
    const message = direct === 0
      ? `No supported photos were found directly in the selected folder, but ${nested} supported photo(s) were found in subfolders. Include subfolders and upload them?`
      : `Folder scan found ${direct} supported photo(s) directly in the selected folder and ${nested} more in subfolders. Include subfolders, or upload only the ${direct} directly listed photo(s)?`
    return { kind: 'ask', directCount: direct, nestedCount: nested, message }
  }
  return finishPhotoPlan(scan, folder, includeSubfolders)
}
/** After the user answered the subfolder question (or none was needed). */
export function resolveAsk(scan: PhotoScan, folder: string, include: boolean): PhotoPlan {
  const direct = Number(scan.direct_count || 0)
  const nested = Number(scan.nested_count || 0)
  if (!include && direct === 0) return { kind: 'error', message: `No upload queued. ${nested} supported photo(s) are in subfolders; enable Include subfolders to send them.` }
  return finishPhotoPlan(scan, folder, include)
}
function finishPhotoPlan(scan: PhotoScan, folder: string, include: boolean): PhotoPlan {
  const count = include ? Number(scan.recursive_count || 0) : Number(scan.direct_count || 0)
  if (count === 0) {
    const seen = Object.entries(scan.recursive_extensions ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([ext, n]) => `${ext}: ${n}`).join(', ') || 'none'
    return { kind: 'error', message: `No supported photos detected in ${folder}. The scan inspected ${Number(scan.recursive_file_count || 0)} file(s). Extensions seen: ${seen}. No upload was queued.` }
  }
  return { kind: 'go', includeSubfolders: include, count }
}

export function startMessage(source: Source, r: { id?: string; detail?: string }, images: boolean, count: number | null): string {
  if (!r.id) return `Could not start: ${r.detail || 'unknown error'}`
  if (source === 'telegram') return 'Telegram media inspection/preparation queued.'
  return images ? `Image document upload queued (${count} supported photo(s) detected). Already-delivered Telegram documents may be verified and reused instead of uploaded again; verified images are indexed in Photos.` : 'Local preparation/transfer queued.'
}

/** A folder already added in Settings → Local files (scanned, with its snapshots and names): uploaded by its own route, so nothing is made or looked up twice. */
export interface ScannedFolder { id: number; path: string; title: string | null; content_rating: 'sfw' | 'nsfw'; videos: number }
export const folderName = (f: Pick<ScannedFolder, 'path' | 'title'>): string => f.title?.trim() || f.path.split(/[\\/]/).filter(Boolean).pop() || f.path
export const folderLabel = (f: ScannedFolder): string => `${folderName(f)} · ${f.content_rating.toUpperCase()} · ${f.videos.toLocaleString()} video${f.videos === 1 ? '' : 's'}`
/** A folder holds one kind, and so does a channel: only the channels of the folder's kind (and never another folder) can receive it. */
export const channelsForFolder = <T extends { role?: string | null; content_rating?: string | null }>(channels: readonly T[], folder: Pick<ScannedFolder, 'content_rating'>): T[] =>
  channels.filter((c) => c.role !== 'local' && (c.content_rating ?? 'nsfw') === folder.content_rating)
export function validateScanned(folder: ScannedFolder | null, destination: string): string {
  if (!folder) return 'Choose a scanned folder.'
  if (!folder.videos) return 'This folder has no videos to upload yet.'
  if (!destination) return 'Choose a destination channel.'
  return ''
}
export function scannedMessage(r: { seeded: number; rebuilt: number; skipped: number; destination: string }): string {
  const ready = r.seeded + r.rebuilt
  return `Upload to ${r.destination} started: ${ready.toLocaleString()} video${ready === 1 ? '' : 's'} with snapshots ready${r.rebuilt ? ` (${r.rebuilt.toLocaleString()} made wider first)` : ''}${r.skipped ? `, ${r.skipped.toLocaleString()} will be prepared by the upload` : ''}. Follow it in Operations.`
}
