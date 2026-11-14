/** Verification frames for a suggested video: four snapshots (10 %, 35 %, 60 %, 85 %) so the operator can check it is the right scene. Ported from the classic preview. */
export const FRACTIONS = [0.1, 0.35, 0.6, 0.85] as const
const SNAPSHOTS = 36

/** Which of the 36 stored snapshots is nearest this fraction of the video (snapshots span 2 %–98 %). */
export const snapshotIndex = (frac: number) => Math.max(0, Math.min(SNAPSHOTS - 1, Math.round(((frac - 0.02) / 0.96) * (SNAPSHOTS - 1))))
/** The percentage shown on the tile: where in the video that snapshot really is. */
export const framePercent = (index: number) => Math.round((0.02 + (0.96 * index) / (SNAPSHOTS - 1)) * 100)
export const snapshotSrc = (cid: string | number, mid: number, index: number) => `/api/play/preview-frame/${encodeURIComponent(String(cid))}/${mid}/${index}?v=8`
/** A frame generated on demand when the stored snapshot is missing. */
export function liveSrc(cid: string | number, mid: number, duration: number, frac: number): string {
  const d = Number(duration) > 0 ? Number(duration) : 0
  const t = d > 0 ? Math.max(0, Math.min(d - 0.05, d * frac)) : 0
  return `/api/play/preview/${encodeURIComponent(String(cid))}/${mid}?t=${encodeURIComponent(t.toFixed(3))}&w=480&pv=17`
}

export type FrameState = 'loading' | 'snapshot' | 'live' | 'failed'
export const INCOMPLETE_NOTICE = 'Some verification frames could not be generated. Check the available previews before linking.'
/** True when every frame has finished and at least one never loaded. */
export const incomplete = (states: readonly FrameState[]) => states.every((s) => s !== 'loading') && states.some((s) => s === 'failed')
export const previewQuestion = (kind: 'performer' | 'studio', name: string) =>
  `Does this video belong to ${name}? Confirming links only this ${kind}. Use Edit Video or Scene Match Review for full scene identification.`
