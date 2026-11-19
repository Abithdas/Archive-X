import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'

export interface CardShape { aspect: number; extra: number }
/** What a card looks like: height = width × `aspect` + `extra` (the text under a picture), in px. */
export const VIDEO_CARD: CardShape = { aspect: 9 / 16, extra: 96 }
export const PERFORMER_CARD: CardShape = { aspect: 3 / 2, extra: 0 }
export const STUDIO_CARD: CardShape = { aspect: 1, extra: 0 }
export const MIN_ROWS = 2
const SMALLEST = 96   // px: below this a card cannot be read

/**
 * How many columns show `n` cards in a `width` × `height` area: at least MIN_ROWS rows (when there are enough cards), the largest cards that still fit
 * the height, and when two choices give almost the same size the one that fills its last row. If nothing fits, the shortest page wins (cards no smaller than SMALLEST).
 */
export function columnsForScreen(n: number, width: number, height: number, gap: number, shape: CardShape): number {
  const count = Math.max(1, Math.floor(n))
  if (!(width > 0)) return 1
  const minRows = count >= MIN_ROWS * 2 ? MIN_ROWS : 1
  const all: Array<{ c: number; w: number; total: number }> = []
  for (let c = 1; c <= count; c++) {
    const rows = Math.ceil(count / c)
    if (rows < minRows) break
    const w = (width - gap * (c - 1)) / c
    if (w < SMALLEST && c > 1) break
    all.push({ c, w, total: rows * (w * shape.aspect + shape.extra) + gap * (rows - 1) })
  }
  if (!all.length) return 1
  const fits = all.filter((o) => o.total <= height)
  if (!fits.length) return all.reduce((a, b) => (b.total < a.total ? b : a)).c
  const best = Math.max(...fits.map((o) => o.w))
  const near = fits.filter((o) => o.w >= best * 0.95)
  return (near.find((o) => count % o.c === 0) ?? near.reduce((a, b) => (b.w > a.w ? b : a))).c
}

/** The page's zoom: the window zoom on big screens times the Settings display size, as the CSS variables hold them. */
export function pageZoom(): number {
  if (typeof document === 'undefined') return 1
  const s = getComputedStyle(document.documentElement)
  const n = (v: string) => { const x = parseFloat(v); return Number.isFinite(x) && x > 0 ? x : 1 }
  return n(s.getPropertyValue('--ax-page-zoom')) * n(s.getPropertyValue('--ax-ui-zoom'))
}

const scroller = (el: HTMLElement | null): HTMLElement | null => {
  for (let p = el?.parentElement ?? null; p; p = p.parentElement) { const o = getComputedStyle(p).overflowY; if (o === 'auto' || o === 'scroll') return p }
  return null
}

export interface ScreenGrid { ref: (el: HTMLElement | null) => void; cols: number; style: CSSProperties; fit: (n: number) => number }

/**
 * A card grid that shows exactly the chosen number of cards: the columns (so the card size) follow the number, the window and the zoom.
 * More cards per page means smaller cards; at least two rows; the page numbers stay under the cards.
 */
export function useScreenGrid(nominal: number, gap: number, shape: CardShape, reserve = 110): ScreenGrid {
  const [cols, setCols] = useState(() => (typeof window === 'undefined' ? 1 : columnsForScreen(nominal, Math.min(window.innerWidth, 1800) - 98, window.innerHeight / pageZoom() - 180, gap, shape)))
  const node = useRef<HTMLElement | null>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const measure = useCallback(() => {
    const el = node.current
    const width = el?.clientWidth ?? 0
    if (!el || width <= 0) return
    const z = pageZoom()
    const box = scroller(el)
    const room = box
      ? box.clientHeight - ((el.getBoundingClientRect().top - box.getBoundingClientRect().top) / z + box.scrollTop) - reserve
      : window.innerHeight / z - el.getBoundingClientRect().top / z - reserve
    setCols(columnsForScreen(nominal, width, Math.max(room, 0), gap, shape))
  }, [nominal, gap, shape, reserve])
  const ref = useCallback((el: HTMLElement | null) => {
    observer.current?.disconnect()
    node.current = el
    if (el && typeof ResizeObserver !== 'undefined') {
      observer.current = new ResizeObserver(measure)
      observer.current.observe(el)
      const box = scroller(el)
      if (box) observer.current.observe(box)
    }
  }, [measure])
  useLayoutEffect(measure)
  useEffect(() => () => observer.current?.disconnect(), [])
  const fit = useCallback((n: number) => n, [])
  return { ref, cols, style: { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }, fit }
}
