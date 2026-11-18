import { useEffect, type RefObject } from 'react'

/**
 * Lets a row that scrolls sideways be moved with a mouse: the wheel scrolls it left and right, and it can be dragged.
 * The wheel only takes over while the row can still move that way, so the page keeps scrolling once the row reaches its end.
 */
export function useScrollRow(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth + 1 || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return   // nothing to scroll, or the device already scrolls sideways
      const max = el.scrollWidth - el.clientWidth
      if ((e.deltaY < 0 && el.scrollLeft <= 0) || (e.deltaY > 0 && el.scrollLeft >= max - 1)) return
      e.preventDefault()
      el.scrollLeft += e.deltaY
    }
    let start = 0
    let from = 0
    let down = false
    let moved = false
    const onDown = (e: PointerEvent) => { if (e.pointerType !== 'mouse' || e.button !== 0) return; down = true; moved = false; start = e.clientX; from = el.scrollLeft }
    const onMove = (e: PointerEvent) => {
      if (!down) return
      const dx = e.clientX - start
      if (!moved && Math.abs(dx) > 5) moved = true
      if (moved) el.scrollLeft = from - dx
    }
    const onUp = () => { down = false }
    const onClick = (e: MouseEvent) => { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false } }   // a drag is not a click on whatever was under the mouse
    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    el.addEventListener('click', onClick, true)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      el.removeEventListener('click', onClick, true)
    }
  }, [ref])
}
