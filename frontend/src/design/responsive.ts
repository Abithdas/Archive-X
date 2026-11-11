/** Display-size copies of stored artwork. The server makes them from the master on request (`?w=`, allowed widths only) and never enlarges. */
export const IMAGE_WIDTHS = [160, 240, 320, 480, 640, 960] as const

/** The URL asking for a copy `width` pixels wide (a query string already on the URL is kept). */
export function withWidth(url: string, width: number): string {
  return `${url}${url.includes('?') ? '&' : '?'}w=${width}`
}
/** `srcset` text for the given widths; `master` is the width of the unmodified file (listed without `w`) when it should be a candidate too. */
export function srcSet(url: string, widths: readonly number[], master?: number): string {
  const parts = widths.filter((w) => !master || w < master).map((w) => `${withWidth(url, w)} ${w}w`)
  if (master) parts.push(`${url} ${master}w`)
  return parts.join(', ')
}
/** The smallest allowed width that covers `cssWidth` at the given device-pixel ratio (the largest one if none does). */
export function widthFor(cssWidth: number, dpr = 2): number {
  const need = cssWidth * dpr
  return IMAGE_WIDTHS.find((w) => w >= need) ?? IMAGE_WIDTHS[IMAGE_WIDTHS.length - 1]!
}
/** Responsive props for an `<img>` of stored artwork: candidates for the widths a layout can need, and the layout's `sizes`. */
export function responsive(url: string, sizes: string, widths: readonly number[] = [240, 320, 480, 640, 960], master?: number) {
  // `src` is what browsers without srcset support (and the first paint of some) use: the unmodified file when its width is known, else a mid-size copy.
  return { src: master ? url : withWidth(url, widths[Math.min(2, widths.length - 1)]!), srcSet: srcSet(url, widths, master), sizes }
}
