/** Hover preview on a video card: its 36 cached snapshots play through, and moving the mouse across the picture scrubs them (as the classic cards did). Local cache only. */
export const FRAME_COUNT = 36
export const FRAME_MS = 300
export const HYSTERESIS_PX = 10

export const clampIndex = (i: number) => Math.max(0, Math.min(FRAME_COUNT - 1, Math.floor(Number(i) || 0)))
export const ratioFromPointer = (clientX: number, rect: { left: number; width: number }) =>
  rect.width > 0 ? Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) : 0
export const indexFromPointer = (clientX: number, rect: { left: number; width: number }) => clampIndex(ratioFromPointer(clientX, rect) * FRAME_COUNT)
export const nextIndex = (i: number) => (clampIndex(i) + 1) % FRAME_COUNT

/** A small dead zone so a jittery mouse does not flip between neighbouring frames. */
export function shouldCommit(lastX: number | null, x: number, current: number, candidate: number, hysteresis = HYSTERESIS_PX): boolean {
  if (lastX === null || !Number.isFinite(lastX)) return true
  if (clampIndex(candidate) === current) return false
  return Math.abs(x - lastX) >= hysteresis
}

/** Where in the video frame `index` is: the manifest's own time when valid, else an even spread. */
export function frameSeconds(times: ReadonlyArray<number> | undefined, index: number, duration: number): number {
  const i = clampIndex(index)
  const t = Number(times?.[i])
  const spread = duration > 0 ? (duration * i) / (FRAME_COUNT - 1) : i
  const s = Number.isFinite(t) && t >= 0 ? t : spread
  return duration > 0 ? Math.min(duration, s) : s
}
/** The 36 frames are only used when all of them are cached; otherwise the card keeps its normal thumbnail and nothing is requested from Telegram. */
export const previewReady = (m: { frames_ready?: boolean; frame_count?: number } | undefined | null) => Boolean(m?.frames_ready) && Number(m?.frame_count ?? 0) >= FRAME_COUNT
export const frameSrc = (cid: string | number, mid: number, index: number, revision = '') =>
  `/api/play/preview-frame/${encodeURIComponent(String(cid))}/${mid}/${clampIndex(index)}${revision ? `?v=${encodeURIComponent(revision)}` : ''}`

/** How far through the video the saved position is, 0–100 (0 when nothing is saved). */
export function watchedPercent(h: { position?: number; duration?: number; completed?: boolean } | null | undefined, fallbackDuration = 0): number {
  if (!h) return 0
  if (h.completed) return 100
  const duration = Number(h.duration) > 0 ? Number(h.duration) : fallbackDuration
  const position = Number(h.position) || 0
  return duration > 0 && position > 0 ? Math.max(0, Math.min(100, (position / duration) * 100)) : 0
}
