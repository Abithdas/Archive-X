import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'

/** How many cards of at least `minCard` px fit side by side in `width` px, with `gap` px between them (always at least 1). */
export function columnsFor(width: number, minCard: number, gap: number): number {
  if (!(width > 0)) return 1
  return Math.max(1, Math.floor((width + gap) / (minCard + gap)))
}

/** The page size nearest to `nominal` that fills whole rows: 12 on 5 columns gives 15, so the last row is never half empty. */
export function fitPer(nominal: number, cols: number): number {
  const c = Math.max(1, Math.floor(cols))
  return Math.max(c, Math.ceil(Math.max(1, nominal) / c) * c)
}

export interface FitGrid {
  ref: (el: HTMLElement | null) => void
  cols: number
  style: CSSProperties
  fit: (nominal: number) => number
}

/**
 * A card grid whose column count is measured, so the page size can be a whole number of rows.
 * `sidePad` is the space the page keeps left and right of the grid; it is only used for the first guess before the grid is measured.
 */
export function useFitGrid(minCard: number, gap: number, sidePad = 98): FitGrid {
  const guess = typeof window === 'undefined' ? 1 : columnsFor(Math.min(window.innerWidth, 1800) - sidePad, minCard, gap)
  const [cols, setCols] = useState(guess)
  const node = useRef<HTMLElement | null>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const measure = useCallback(() => {
    const w = node.current?.clientWidth ?? 0
    if (w > 0) setCols(columnsFor(w, minCard, gap))
  }, [minCard, gap])
  const ref = useCallback((el: HTMLElement | null) => {
    observer.current?.disconnect()
    node.current = el
    if (el && typeof ResizeObserver !== 'undefined') {
      observer.current = new ResizeObserver(measure)
      observer.current.observe(el)
    }
  }, [measure])
  useLayoutEffect(measure)
  useEffect(() => () => observer.current?.disconnect(), [])
  const fit = useCallback((nominal: number) => fitPer(nominal, cols), [cols])
  return { ref, cols, style: { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }, fit }
}
