/** Picture and sound for the web player: zoom, picture enhancement (sharpening and richer colour, a filter on the video) and the sound mode and volume boost. Kept on this PC. */
export type Zoom = 'off' | 'fill' | '110' | '125' | '133'
export const ZOOMS: Array<[Zoom, string]> = [['off', 'Off'], ['fill', 'Remove black bars'], ['110', '110%'], ['125', '125%'], ['133', '133%']]
export type Sound = 'normal' | 'dialogue' | 'night'
export const SOUNDS: Array<[Sound, string]> = [['normal', 'Normal'], ['dialogue', 'Dialogue boost'], ['night', 'Night mode']]
export type Enhance = 'off' | 'sharpen' | 'vivid' | 'both'
export const ENHANCES: Array<[Enhance, string]> = [['off', 'Off'], ['sharpen', 'Sharpen'], ['vivid', 'Richer colour'], ['both', 'Sharpen + colour']]
export const SHARPEN_ID = 'ax-sharpen'
export interface PictureSound { boost: number; zoom: Zoom; sound: Sound; enhance: Enhance }
export const DEFAULT_PS: PictureSound = { boost: 100, zoom: 'off', sound: 'normal', enhance: 'off' }
export const PS_RANGE = { boost: [100, 300] } as const
const KEY = 'archive-x:picture-sound'

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(n)))
export function normalizePS(v: Partial<PictureSound> | null | undefined): PictureSound {
  const out: PictureSound = { ...DEFAULT_PS }
  if (ZOOMS.some(([z]) => z === v?.zoom)) out.zoom = v!.zoom as Zoom
  if (SOUNDS.some(([z]) => z === v?.sound)) out.sound = v!.sound as Sound
  if (ENHANCES.some(([z]) => z === v?.enhance)) out.enhance = v!.enhance as Enhance
  for (const k of Object.keys(PS_RANGE) as Array<keyof typeof PS_RANGE>) {
    const n = Number(v?.[k])
    if (v?.[k] !== undefined && Number.isFinite(n)) out[k] = clamp(n, PS_RANGE[k][0], PS_RANGE[k][1])
  }
  return out
}
export const isDefaultPS = (s: PictureSound) => (Object.keys(DEFAULT_PS) as Array<keyof PictureSound>).every((k) => s[k] === DEFAULT_PS[k])
/** The `filter` value for the video, or undefined when the picture is untouched (no filter layer at all). Sharpen points at the SVG filter the player draws. */
export const pictureFilter = (s: PictureSound): string | undefined => {
  const parts = [s.enhance === 'sharpen' || s.enhance === 'both' ? `url(#${SHARPEN_ID})` : '', s.enhance === 'vivid' || s.enhance === 'both' ? 'contrast(1.06) saturate(1.2)' : ''].filter(Boolean)
  return parts.length ? parts.join(' ') : undefined
}
export function loadPS(): PictureSound { try { return normalizePS(JSON.parse(localStorage.getItem(KEY) ?? 'null')) } catch { return DEFAULT_PS } }
export function savePS(s: PictureSound) { try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private mode */ } }
/** How much to enlarge the picture. `fill` removes the bars around a video that does not match its frame (the whole window in full screen); `box` is the frame's shape, width over height. */
export function zoomScale(zoom: Zoom, videoRatio: number, boxRatio: number): number {
  if (zoom === 'off') return 1
  if (zoom === 'fill') return Math.min(2, Math.max(1, boxRatio / videoRatio, videoRatio / boxRatio))
  return Number(zoom) / 100
}
