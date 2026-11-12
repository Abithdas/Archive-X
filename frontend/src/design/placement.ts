/** The app is zoomed (the Appearance scale, and the automatic zoom on large screens), and a `fixed` offset is measured in zoomed pixels
 *  while getBoundingClientRect() and window.innerWidth are real screen pixels. These helpers turn screen pixels into the pixels a menu is positioned in. */
export function zoomOf(el: Element | null | undefined): number {
  const h = el as HTMLElement | null | undefined
  const shown = h?.offsetWidth ?? 0
  const real = h?.getBoundingClientRect().width ?? 0
  const z = shown > 0 && real > 0 ? real / shown : 1
  return Number.isFinite(z) && z > 0 ? z : 1
}
/** Screen pixels → positioning pixels for an element that lives in the same zoom as `ref`. */
export const local = (px: number, ref: Element | null | undefined) => px / zoomOf(ref)

/** Left, top and maximum height for a menu `w`×`h` (positioning pixels) opened from `anchor`: below the button when it fits, else above, else on the roomier side
 *  with a limited height (so it scrolls) instead of covering the button or leaving the screen. */
export function dropdownPosition(anchor: DOMRect, ref: Element | null | undefined, w: number, h: number, align: 'left' | 'right' = 'right', screen = { w: window.innerWidth, h: window.innerHeight }) {
  const z = zoomOf(ref)
  const sw = screen.w / z, sh = screen.h / z
  const l = anchor.left / z, r = anchor.right / z, t = anchor.top / z, b = anchor.bottom / z
  const left = Math.max(8, Math.min(sw - w - 8, align === 'right' ? r - w : l))
  const below = sh - b - 12, above = t - 12
  if (h <= below) return { left, top: b + 4, maxHeight: below }
  if (h <= above) return { left, top: t - h - 4, maxHeight: above }
  return below >= above ? { left, top: b + 4, maxHeight: Math.max(120, below) } : { left, top: Math.max(8, t - Math.max(120, above) - 4), maxHeight: Math.max(120, above) }
}
