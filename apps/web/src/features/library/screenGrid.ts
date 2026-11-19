import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'

export interface CardShape { aspect: number; extra: number }
/** What a card looks like: height = width × `aspect` + `extra` (the text under a picture), in px. */
export const VIDEO_CARD: CardShape = { aspect: 9 / 16, extra: 96 }
export const PERFORMER_CARD: CardShape = { aspect: 3 / 2, extra: 0 }
export const STUDIO_CARD: CardShape = { aspect: 1, extra: 0 }
/** The card width (CSS px at 100%) "Auto" aims for; the columns are the most that keep cards at least this wide. */
export const VIDEO_TARGET = 236
export const PERFORMER_TARGET = 180
export const STUDIO_TARGET = 280
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

/**
 * "Auto": as many cards as the screen holds. The columns are what fit at the target width; if two rows would then be taller than the room, cards
 * get narrower until two rows fit. Returns the columns and the rows, so a page holds cols × rows cards.
 */
export function autoLayout(width: number, height: number, gap: number, shape: CardShape, target: number): { cols: number; rows: number } {
  if (!(width > 0)) return { cols: 1, rows: MIN_ROWS }
  const start = Math.max(1, Math.floor((width + gap) / (target + gap)))
  let c = start
  for (;; c++) {
    const w = (width - gap * (c - 1)) / c
    const h = w * shape.aspect + shape.extra
    const rows = Math.floor((height + gap) / (h + gap))
    if (rows >= MIN_ROWS) return { cols: c, rows }
    if (w < SMALLEST) break
  }
  return { cols: start, rows: MIN_ROWS }
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

export interface ScreenGrid { ref: (el: HTMLElement | null) => void; cols: number; rows: number; per: number; style: CSSProperties }

/**
 * A card grid sized to the screen and the zoom. `nominal` 0 is "Auto": the page holds as many cards as fit (at least two rows) and `per` says how many.
 * A number: that many cards per page, the columns (so the card size) following it; more cards per page means smaller cards.
 */
export function useScreenGrid(nominal: number, gap: number, shape: CardShape, target: number, reserve = 170): ScreenGrid {
  const auto = nominal <= 0
  const compute = useCallback((width: number, room: number): { cols: number; rows: number } => {
    if (auto) return autoLayout(width, room, gap, shape, target)
    const cols = columnsForScreen(nominal, width, room, gap, shape)
    return { cols, rows: Math.ceil(nominal / cols) }
  }, [auto, nominal, gap, shape, target])
  const [layout, setLayout] = useState(() => (typeof window === 'undefined' ? { cols: 1, rows: 2 } : compute(Math.min(window.innerWidth, 1800) - 98, window.innerHeight / pageZoom() - 180 - 40)))
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
    const next = compute(width, Math.max(room, 0))
    setLayout((l) => (l.cols === next.cols && l.rows === next.rows ? l : next))
  }, [compute, reserve])
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
  const per = auto ? layout.cols * layout.rows : nominal
  return { ref, cols: layout.cols, rows: layout.rows, per, style: { gridTemplateColumns: `repeat(${layout.cols}, minmax(0, 1fr))` } }
}
