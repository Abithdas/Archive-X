/** Picture and sound for the web player: brightness, contrast and colour (a CSS filter on the video) and a volume boost (a gain in the audio graph). Kept on this PC. */
export type Zoom = 'off' | 'fill' | '110' | '125' | '133'
export const ZOOMS: Array<[Zoom, string]> = [['off', 'Off'], ['fill', 'Remove black bars'], ['110', '110%'], ['125', '125%'], ['133', '133%']]
export type Sound = 'normal' | 'dialogue' | 'night'
export const SOUNDS: Array<[Sound, string]> = [['normal', 'Normal'], ['dialogue', 'Dialogue boost'], ['night', 'Night mode']]
export interface PictureSound { brightness: number; contrast: number; saturation: number; boost: number; zoom: Zoom; sound: Sound }
export const DEFAULT_PS: PictureSound = { brightness: 100, contrast: 100, saturation: 100, boost: 100, zoom: 'off', sound: 'normal' }
export const PS_RANGE = { brightness: [50, 150], contrast: [50, 150], saturation: [0, 200], boost: [100, 300] } as const
const KEY = 'archive-x:picture-sound'

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(n)))
export function normalizePS(v: Partial<PictureSound> | null | undefined): PictureSound {
  const out: PictureSound = { ...DEFAULT_PS }
  if (ZOOMS.some(([z]) => z === v?.zoom)) out.zoom = v!.zoom as Zoom
  if (SOUNDS.some(([z]) => z === v?.sound)) out.sound = v!.sound as Sound
  for (const k of Object.keys(PS_RANGE) as Array<keyof typeof PS_RANGE>) {
    const n = Number(v?.[k])
    if (v?.[k] !== undefined && Number.isFinite(n)) out[k] = clamp(n, PS_RANGE[k][0], PS_RANGE[k][1])
  }
  return out
}
export const isDefaultPS = (s: PictureSound) => (Object.keys(DEFAULT_PS) as Array<keyof PictureSound>).every((k) => s[k] === DEFAULT_PS[k])
/** The `filter` value for the video, or undefined when the picture is untouched (no filter layer at all). */
export const pictureFilter = (s: PictureSound): string | undefined =>
  s.brightness === 100 && s.contrast === 100 && s.saturation === 100 ? undefined : `brightness(${s.brightness}%) contrast(${s.contrast}%) saturate(${s.saturation}%)`
export function loadPS(): PictureSound { try { return normalizePS(JSON.parse(localStorage.getItem(KEY) ?? 'null')) } catch { return DEFAULT_PS } }
export function savePS(s: PictureSound) { try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private mode */ } }
/** How much to enlarge the picture. `fill` removes the bars around a video that does not match its frame (the whole window in full screen); `box` is the frame's shape, width over height. */
export function zoomScale(zoom: Zoom, videoRatio: number, boxRatio: number): number {
  if (zoom === 'off') return 1
  if (zoom === 'fill') return Math.min(2, Math.max(1, boxRatio / videoRatio, videoRatio / boxRatio))
  return Number(zoom) / 100
}
