/** Pure logic for the Photos page and viewer (mirrors the classic photo library). */

export const FAVORITES_KEY = 'archive-x-photo-favorites-v1'
export const PER_PAGE = 48
export type PhotoSort = 'date_desc' | 'date_asc' | 'name_asc' | 'size_desc'
export const SORTS: Array<{ value: PhotoSort; label: string }> = [
  { value: 'date_desc', label: 'Newest first' }, { value: 'date_asc', label: 'Oldest first' },
  { value: 'name_asc', label: 'Name A–Z' }, { value: 'size_desc', label: 'Largest first' },
]
export const sortLabel = (v: string) => SORTS.find((s) => s.value === v)?.label ?? SORTS[0]!.label

export interface PhotoChannel { telegram_id: string | number; title?: string | null; image?: string | null; updated_at?: string | null; photo_count?: number; photo_scan_status?: string; photo_last_scan_at?: string | null }
export interface Photo { id: number; channel_id: string; message_id: number; filename: string; caption?: string; mime_type?: string; size: number; width: number; height: number; message_date?: string | null; channel_title?: string; thumbnail_url: string; preview_url: string; image_url: string }
export interface PhotoPage { ok?: boolean; detail?: string; items: Photo[]; total: number; page: number; per: number }

export const NO_CHANNELS_SAVED = 'No saved Telegram channels found. Open Videos and refresh the Telegram channel list first.'
export const NO_SCANNED = 'No channels have a completed Image scan yet. Open Transfers → Metadata & Assets Manager → Processing Stages → Advanced options, enable Image scan, select channels, and run it.'
export const SELECT_PROMPT = 'Select channel cards to browse indexed photos. To index channel history, use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.'
export const EMPTY_INDEX = 'No photos indexed in these channels yet. Run Image scan in Metadata & Assets Manager → Processing Stages → Advanced options.'
export const LOAD_FAILED = 'Could not load photo records.'

export const channelTitle = (c: Pick<PhotoChannel, 'telegram_id' | 'title'>) => {
  const t = String(c.title ?? '').trim()
  return t && !/^unknown channel$/i.test(t) && !/^channel\s+-?\d+$/i.test(t) && !/^-?\d+$/.test(t) ? t : 'Unknown channel'
}
export const initials = (title: string) => title.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => Array.from(p)[0] ?? '').join('').toUpperCase() || 'TG'

/** Only channels with a completed image scan are browsable, sorted by name. Rows from the photo index are merged over the saved list. */
export function scannedChannels(saved: readonly PhotoChannel[], photo: readonly PhotoChannel[]): PhotoChannel[] {
  const byId = new Map(photo.map((c) => [String(c.telegram_id), c]))
  const seen = new Set<string>()
  const merged: PhotoChannel[] = saved.map((c) => {
    const id = String(c.telegram_id)
    seen.add(id)
    const p = byId.get(id)
    return { ...(p ?? {}), ...c, photo_count: Number(p?.photo_count ?? c.photo_count ?? 0), photo_scan_status: String(p?.photo_scan_status || c.photo_scan_status || 'NEVER').toUpperCase(), photo_last_scan_at: p?.photo_last_scan_at ?? null }
  })
  for (const c of photo) { const id = String(c.telegram_id); if (id && !seen.has(id)) { merged.push(c); seen.add(id) } }
  return merged.filter((c) => String(c.photo_scan_status || 'NEVER').toUpperCase() === 'COMPLETE').sort((a, b) => channelTitle(a).localeCompare(channelTitle(b)))
}
export const channelAvatar = (c: PhotoChannel) => {
  const id = encodeURIComponent(String(c.telegram_id))
  const v = encodeURIComponent(String(c.updated_at || '0'))
  return String(c.image || '').startsWith('manual-upload:') ? `/api/channels/${id}/image?v=${v}&w=160` : `/api/channels/${id}/telegram-photo?v=${v}`
}
export const scanTitle = (c: PhotoChannel) => `${c.photo_last_scan_at ? `Last completed image scan ${new Date(c.photo_last_scan_at).toLocaleString()}` : 'Image scan complete'} · ${Number(c.photo_count || 0).toLocaleString()} photos`
export const channelSummary = (n: number) => `${n.toLocaleString()} scanned channels`
export const selectedLabel = (n: number) => `${n} channel${n === 1 ? '' : 's'} selected`

export function toggleId(selected: readonly string[], id: string): string[] { return selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id] }
export const allSelected = (channels: readonly PhotoChannel[], selected: readonly string[]) => channels.length > 0 && channels.every((c) => selected.includes(String(c.telegram_id)))
export const toggleAll = (channels: readonly PhotoChannel[], selected: readonly string[]) => (allSelected(channels, selected) ? [] : channels.map((c) => String(c.telegram_id)))
/** Selection limited to channels that still exist (a removed channel can't stay selected). */
export const pruneSelection = (selected: readonly string[], channels: readonly PhotoChannel[]) => selected.filter((id) => channels.some((c) => String(c.telegram_id) === id))

export type FaceScope = 'named' | 'unnamed' | 'no_face' | 'pending'
export interface FaceFilter { performerId: number | null; groupIds: number[]; groupId: number | null; label: string; scope?: FaceScope }
export interface PhotoQuery { channels: readonly string[]; q: string; sort: PhotoSort; favorites: boolean; favoriteIds: ReadonlySet<string>; page: number; per?: number; face?: FaceFilter | null; contentTags?: readonly string[] }
export function photoParams(q: PhotoQuery): URLSearchParams {
  const p = new URLSearchParams({ page: String(q.page), per: String(q.per ?? PER_PAGE), channels: q.channels.join(','), q: q.q, sort: q.sort })
  const f = q.face
  if (f?.performerId) p.set('face_performer_id', String(f.performerId))
  else if (f?.groupIds.length) p.set('face_group_ids', f.groupIds.join(','))
  else if (f?.groupId && f.groupId > 0) p.set('face_group_id', String(f.groupId))
  if (f?.scope) p.set('face_scope', f.scope)
  if (q.contentTags?.length) p.set('content_tags', q.contentTags.join(','))
  if (q.favorites) { p.set('favorites', '1'); p.set('favorite_ids', [...q.favoriteIds].slice(0, 900).join(',')) }
  return p
}
export const photosUrl = (q: PhotoQuery) => `/api/photos?${photoParams(q)}`

export function readFavorites(storage: Pick<Storage, 'getItem'> | null): Set<string> {
  try { const v = JSON.parse(storage?.getItem(FAVORITES_KEY) || '[]') as unknown; return new Set(Array.isArray(v) ? v.map(String) : []) } catch { return new Set() }
}
export function writeFavorites(storage: Pick<Storage, 'setItem'> | null, ids: ReadonlySet<string>) { try { storage?.setItem(FAVORITES_KEY, JSON.stringify([...ids])) } catch { /* storage may be disabled */ } }
export const favKey = (p: Pick<Photo, 'id'>) => String(p.id || '')
export function toggleFavorite(ids: ReadonlySet<string>, p: Pick<Photo, 'id'>): Set<string> { const k = favKey(p); const n = new Set(ids); if (!k) return n; if (n.has(k)) n.delete(k); else n.add(k); return n }

export const emptyText = (search: string, favorites: boolean) => search ? { title: 'No photos match this search.', body: 'Try another filename, caption, or channel name.' }
  : favorites ? { title: 'No favourite photos here yet.', body: 'Star a photo in the viewer to find it here.' }
  : { title: 'No photos indexed in selected channels yet.', body: 'Use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.' }
export const summaryText = (total: number) => `${total.toLocaleString()} image${total === 1 ? '' : 's'}`

export const photoName = (p: Pick<Photo, 'filename' | 'message_id'>) => String(p.filename || `Photo ${p.message_id}`)
export const ratio = (p: Pick<Photo, 'width' | 'height'>) => (p.width > 0 && p.height > 0 ? `${p.width} / ${p.height}` : '4 / 3')

export function formatBytes(v: unknown): string {
  const b = Number(v || 0)
  if (!(b > 0)) return ''
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}
export function formatDate(v: unknown): string {
  if (!v) return ''
  const s = String(v)
  const d = /^\d{10,13}$/.test(s) ? new Date(Number(s) < 1e11 ? Number(s) * 1000 : Number(s)) : new Date(s)
  return Number.isFinite(d.getTime()) ? new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: '2-digit' }).format(d) : s.slice(0, 24)
}
export interface Hud { title: string; dims: string; mp: string; size: string; date: string; channel: string }
export function hud(p: Photo): Hud {
  const ok = p.width > 0 && p.height > 0
  const d = formatDate(p.message_date)
  return { title: photoName(p), dims: ok ? `${p.width} × ${p.height}` : '', mp: ok ? `${((p.width * p.height) / 1e6).toFixed(1)} MP` : '', size: formatBytes(p.size), date: d ? `Message · ${d}` : '', channel: String(p.channel_title || '') }
}
export const counterText = (index: number, total: number) => `${Math.max(0, index) + 1} / ${Math.max(1, total)}`

export const ZOOM_STEP = 1.25
export const MAX_ZOOM = 8
/** How far below the fitted size the photo may be shrunk (a tenth of it). */
export const MIN_REL = 0.1
/** Fit scale for an image shown inside a stage, accounting for 90° rotations. Never upscales past 1. */
export function fitScale(img: { w: number; h: number }, stage: { w: number; h: number }, rotation: number): number {
  const turned = Math.abs(rotation) % 180 === 90
  const w = turned ? img.h : img.w, h = turned ? img.w : img.h
  if (!(w > 0 && h > 0 && stage.w > 0 && stage.h > 0)) return 1
  return Math.min(1, stage.w / w, stage.h / h)
}
export const zoomBy = (scale: number, direction: number, fit: number) => Math.min(MAX_ZOOM, Math.max(fit * MIN_REL, scale * (direction < 0 ? 1 / ZOOM_STEP : ZOOM_STEP)))
export const zoomPercent = (scale: number, fit: number) => `${Math.round((fit > 0 ? scale / fit : 1) * 100)}%`
export const rotate = (current: number, delta: number) => (((current + delta) % 360) + 360) % 360
/** Keep a panned image inside the stage. A photo larger than the stage can move until its far edge reaches the stage edge; a smaller one can be placed anywhere in the stage. */
export function clampPan(x: number, y: number, img: { w: number; h: number }, stage: { w: number; h: number }, scale: number, rotation: number) {
  const turned = Math.abs(rotation) % 180 === 90
  const w = (turned ? img.h : img.w) * scale, h = (turned ? img.w : img.h) * scale
  const mx = Math.abs(w - stage.w) / 2, my = Math.abs(h - stage.h) / 2
  return { x: Math.min(mx, Math.max(-mx, x)), y: Math.min(my, Math.max(-my, y)) }
}
/** Next viewer index; manual steps stop at the ends, slideshow playback wraps. */
export function stepIndex(i: number, delta: number, total: number, wrap = false): number | null {
  if (total <= 0) return null
  const t = i + delta
  if (t >= 0 && t < total) return t
  return wrap ? (t < 0 ? total - 1 : 0) : null
}
export const STRIP_WINDOW = 24
/** The filmstrip shows a window of thumbnails centred on the current photo. */
export function stripRange(index: number, total: number, size = STRIP_WINDOW): [number, number] {
  const start = Math.max(0, Math.min(index - Math.floor(size / 2), total - size))
  return [start, Math.min(total, start + size)]
}
export const SLIDE_MS = 3000
export const SWIPE_PX = 60

export const deleteCopy = (remote: boolean, name: string, channel: string) => remote
  ? { title: 'Delete photo and Telegram message?', ok: 'Delete from both', text: `Permanently delete "${name}" from Archive X and its Telegram message in "${channel}"?\n\nThis cannot be undone. Archive X will keep the cached image if Telegram deletion fails.` }
  : { title: 'Remove photo from Archive X?', ok: 'Remove', text: `Remove "${name}" from the Archive X Photos library and delete its local cached copies?\n\nThe Telegram message will remain and may be indexed again if its channel is scanned in the future.` }
export const deleteBody = (remote: boolean) => ({ scope: remote ? 'archive_and_telegram' : 'archive_only' })
export const deletePath = (p: Pick<Photo, 'channel_id' | 'message_id'>) => `/api/photos/${encodeURIComponent(p.channel_id)}/${p.message_id}/delete`
export const deleteStatus = (remote: boolean, name: string) => (remote ? `Deleting ${name} from Telegram and Archive X…` : `Removing ${name} from Archive X…`)
export const deleteDone = (remote: boolean, r: { message?: string }) => r.message || (remote ? 'Photo deleted from Archive X and Telegram.' : 'Photo removed from Archive X.')

/** Pictures worth fetching ahead while one photo is open: the next two and the previous one, the full image first and the preview as a quick fallback. */
export function neighborUrls(items: ReadonlyArray<{ preview_url?: string; image_url?: string }>, index: number): string[] {
  return [index + 1, index + 2, index - 1].flatMap((i) => [items[i]?.image_url, items[i]?.preview_url]).filter((u): u is string => Boolean(u))
}

/** Mouse wheel zoom: a smooth exponential factor (same feel as the classic viewer), whatever unit the wheel reports. */
export function wheelFactor(deltaY: number, deltaMode = 0, stageHeight = 800): number {
  const px = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * stageHeight : deltaY
  return Math.exp(-Math.max(-2400, Math.min(2400, px)) * 0.0018)
}
/** New pan after changing the scale from `from` to `to` while keeping the point under the cursor fixed. `cursor` is measured from the stage centre. */
export function anchorPan(pan: { x: number; y: number }, from: number, to: number, cursor: { x: number; y: number }) {
  const k = from > 0 ? to / from : 1
  return { x: cursor.x - (cursor.x - pan.x) * k, y: cursor.y - (cursor.y - pan.y) * k }
}
/** Wheel over the toolbar or filmstrip turns pages: one photo per notch, up to three for a big spin. 0 when there is no movement. */
export function wheelSteps(deltaX: number, deltaY: number): number {
  const raw = Math.abs(deltaX) > Math.abs(deltaY) ? deltaX : deltaY
  if (!raw) return 0
  return (raw > 0 ? 1 : -1) * Math.max(1, Math.min(3, Math.round(Math.abs(raw) / 120) || 1))
}
export const clampScale = (scale: number, fit: number) => Math.min(MAX_ZOOM, Math.max(fit * MIN_REL, scale))
/** Space kept clear around a fitted photo: top and bottom, and the sides. */
export const PAD_Y = 20
export const PAD_X = 16
export const paddedBox = (box: { w: number; h: number }) => ({ w: Math.max(1, box.w - 2 * PAD_X), h: Math.max(1, box.h - 2 * PAD_Y) })
/** Two-finger pinch: the scale follows how far the fingers moved apart or together. */
export const pinchScale = (startScale: number, startDistance: number, distance: number) => (startDistance > 0 ? startScale * (distance / startDistance) : startScale)
