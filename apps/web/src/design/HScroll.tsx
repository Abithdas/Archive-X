import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Icon } from './Icon'
import { cn } from './cn'

/**
 * How far a mouse wheel should move a row sideways, or null to leave the wheel alone.
 * A wheel that turns up and down moves the row; a sideways gesture (trackpad, shift + wheel) is already handled by the browser, and at either
 * end of the row the wheel goes back to scrolling the page.
 */
export function wheelShift(deltaX: number, deltaY: number, scrollLeft: number, maxScroll: number): number | null {
  if (maxScroll <= 0 || Math.abs(deltaY) <= Math.abs(deltaX) || deltaY === 0) return null
  if (deltaY < 0 && scrollLeft <= 0) return null
  if (deltaY > 0 && scrollLeft >= maxScroll) return null
  return deltaY
}

/** Where the arrows are needed: something is hidden on the left, something on the right. */
export function overflowState(scrollLeft: number, clientWidth: number, scrollWidth: number): { left: boolean; right: boolean } {
  return { left: scrollLeft > 1, right: scrollLeft + clientWidth < scrollWidth - 1 }
}

const DRAG_START_PX = 5

/**
 * A row that scrolls sideways by touch (the browser's own), mouse wheel, mouse drag and two arrow buttons that appear only when
 * something is out of view. The scroll bar stays hidden.
 */
export function HScroll({ children, label, className, arrowTop = 24 }: { children: ReactNode; label: string; className?: string; arrowTop?: number }) {
  const box = useRef<HTMLDivElement>(null)
  const [edge, setEdge] = useState({ left: false, right: false })
  const drag = useRef<{ x: number; left: number; active: boolean } | null>(null)
  const dragged = useRef(false)

  const measure = useCallback(() => {
    const el = box.current
    if (el) setEdge(overflowState(el.scrollLeft, el.clientWidth, el.scrollWidth))
  }, [])

  useEffect(() => {
    const el = box.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const by = wheelShift(e.deltaX, e.deltaY, el.scrollLeft, el.scrollWidth - el.clientWidth)
      if (by === null) return
      e.preventDefault()   // this is why the listener is native: React's wheel handler cannot cancel the page scroll
      el.scrollLeft += by
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    ro?.observe(el)
    if (el.firstElementChild) ro?.observe(el.firstElementChild)
    measure()
    return () => { el.removeEventListener('wheel', onWheel); ro?.disconnect() }
  }, [measure, children])

  const page = (dir: -1 | 1) => box.current?.scrollBy({ left: dir * Math.max(120, box.current.clientWidth * 0.8), behavior: 'smooth' })

  return (
    <div className="relative">
      <div
        ref={box}
        role="group"
        aria-label={label}
        tabIndex={0}
        onScroll={measure}
        onKeyDown={(e) => { if (e.key === 'ArrowRight') page(1); else if (e.key === 'ArrowLeft') page(-1) }}
        onPointerDown={(e) => {
          if (e.pointerType !== 'mouse' || e.button !== 0 || !box.current) return
          drag.current = { x: e.clientX, left: box.current.scrollLeft, active: false }
          dragged.current = false
        }}
        onPointerMove={(e) => {
          const d = drag.current
          const el = box.current
          if (!d || !el) return
          const dx = e.clientX - d.x
          if (!d.active && Math.abs(dx) < DRAG_START_PX) return
          if (!d.active) { d.active = true; dragged.current = true; el.setPointerCapture(e.pointerId) }
          el.scrollLeft = d.left - dx
        }}
        onPointerUp={(e) => { const el = box.current; if (drag.current?.active && el?.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId); drag.current = null }}
        onPointerCancel={() => { drag.current = null }}
        onClickCapture={(e) => { if (dragged.current) { e.stopPropagation(); e.preventDefault(); dragged.current = false } }}   // the end of a drag is not a click on a person
        className={cn('overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden [@media(hover:hover)]:cursor-grab [&:active]:[@media(hover:hover)]:cursor-grabbing', className)}
      >
        {children}
      </div>
      {edge.left ? <Arrow dir={-1} top={arrowTop} onClick={() => page(-1)} /> : null}
      {edge.right ? <Arrow dir={1} top={arrowTop} onClick={() => page(1)} /> : null}
    </div>
  )
}

/** `top` is the height, from the top of the row, of the middle of what the arrows belong to (the row's own padding included). */
function Arrow({ dir, top, onClick }: { dir: -1 | 1; top: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={dir < 0 ? 'Scroll left' : 'Scroll right'}
      style={{ top }}
      className={cn('absolute hidden size-8 -translate-y-1/2 place-items-center rounded-full border border-line-2 bg-surface/90 text-fg shadow-md backdrop-blur hover:bg-surface-2 [@media(hover:hover)]:grid', dir < 0 ? 'left-0' : 'right-0')}
    >
      <Icon name={dir < 0 ? 'arrow-left' : 'arrow-right'} className="size-4" />
    </button>
  )
}
