export type CaptionBg = 'none' | 'soft' | 'solid'
/** How captions look and where they sit: text size (1 = normal), background, and the centre of the text as a fraction of the picture. */
export interface CaptionStyle { size: number; bg: CaptionBg; x: number; y: number }
export const DEFAULT_STYLE: CaptionStyle = { size: 1, bg: 'none', x: 0.5, y: 0.88 }
export const SIZE_MIN = 0.6
export const SIZE_MAX = 2.4
export const SIZE_STEP = 0.15
const KEY = 'archive-x:caption-style'

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n))
export const clampSize = (n: number) => Math.round(clamp(n, SIZE_MIN, SIZE_MAX) * 100) / 100
/** Keep the text centre inside the picture, with a margin so it is always reachable. */
export const clampPos = (n: number) => clamp(n, 0.06, 0.94)

export function normalizeStyle(v: Partial<CaptionStyle> | null | undefined): CaptionStyle {
  const bg = v?.bg === 'soft' || v?.bg === 'solid' || v?.bg === 'none' ? v.bg : DEFAULT_STYLE.bg
  return {
    size: Number.isFinite(Number(v?.size)) ? clampSize(Number(v!.size)) : DEFAULT_STYLE.size,
    bg,
    x: Number.isFinite(Number(v?.x)) ? clampPos(Number(v!.x)) : DEFAULT_STYLE.x,
    y: Number.isFinite(Number(v?.y)) ? clampPos(Number(v!.y)) : DEFAULT_STYLE.y,
  }
}
export function loadStyle(): CaptionStyle { try { return normalizeStyle(JSON.parse(localStorage.getItem(KEY) ?? 'null')) } catch { return DEFAULT_STYLE } }
export function saveStyle(s: CaptionStyle) { try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private mode */ } }
export const sizeLabel = (size: number) => `${Math.round(size * 100)}%`
