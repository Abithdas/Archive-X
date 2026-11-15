/** The maths of the image zoom, kept apart from the component so it can be checked on its own. */
export interface Box { left: number; top: number; width: number; height: number }

/** Where the opened image goes: centred, half the window wide (so it can really be reviewed), never taller than 82% of the window, in the image's own shape. */
export function targetBox(vw: number, vh: number, aspect: number): Box {
  const a = Number.isFinite(aspect) && aspect > 0 ? aspect : 16 / 9
  let width = Math.min(Math.max(vw * 0.5, 360), vw - 32)
  let height = width / a
  const maxH = vh * 0.82
  if (height > maxH) { height = maxH; width = height * a }
  return { left: Math.round((vw - width) / 2), top: Math.round((vh - height) / 2), width: Math.round(width), height: Math.round(height) }
}

export interface Flight { dx: number; dy: number; scale: number; clip: [number, number]; tiltX: number; tiltY: number }

/**
 * From the small thumbnail to the target box: one even scale (the thumbnail is usually a crop, so a clip hides what the thumbnail did not
 * show and opens as the image grows), a move to the thumbnail's centre, and a 3D tilt that leans away from the screen centre and settles flat.
 */
export function flight(from: Box, to: Box, vw: number, vh: number): Flight {
  const scale = Math.max(from.width / to.width, from.height / to.height)
  const cx = from.left + from.width / 2, cy = from.top + from.height / 2
  const dx = cx - (to.left + to.width / 2), dy = cy - (to.top + to.height / 2)
  const clipX = Math.max(0, (to.width - from.width / scale) / 2)
  const clipY = Math.max(0, (to.height - from.height / scale) / 2)
  const lean = (d: number, span: number, max: number) => Math.max(-max, Math.min(max, (d / span) * max))
  return { dx, dy, scale, clip: [clipY, clipX], tiltY: lean(dx, vw / 3, 14), tiltX: -lean(dy, vh / 3, 9) }
}

const px = (n: number) => `${n.toFixed(2)}px`
/** The thumbnail's own corner radius (rounded-lg), seen through the starting scale. */
const THUMB_RADIUS = 8
/** The frames of the opening (and, run the other way with fresh numbers, the closing). */
export function frames(f: Flight, radius = 14): Array<{ transform: string; clipPath: string; offset: number }> {
  const t = (x: number, y: number, s: number, rx: number, ry: number) => `translate(${px(x)}, ${px(y)}) scale(${s.toFixed(4)}) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`
  // `k` is how much of the crop is still hidden (1 at the thumbnail, 0 when fully open); the corner rounds from the thumbnail's to the opened image's.
  const clip = (k: number, scale: number) => `inset(${px(f.clip[0] * k)} ${px(f.clip[1] * k)} ${px(f.clip[0] * k)} ${px(f.clip[1] * k)} round ${px((THUMB_RADIUS * k + radius * (1 - k)) / scale)})`
  const mid = f.scale + (1 - f.scale) * 0.58
  return [
    { transform: t(f.dx, f.dy, f.scale, 0, 0), clipPath: clip(1, f.scale), offset: 0 },
    { transform: t(f.dx * 0.42, f.dy * 0.42, mid, f.tiltX, f.tiltY), clipPath: clip(0.45, mid), offset: 0.45 },
    { transform: t(0, 0, 1, 0, 0), clipPath: clip(0, 1), offset: 1 },
  ]
}
