/** Card glow: one document-level listener tells the card under the pointer where the pointer is (CSS draws the light), so no card carries its own handler. */
export function installGlow(): () => void {
  if (typeof document === 'undefined' || !window.matchMedia?.('(hover: hover)').matches) return () => undefined
  let frame = 0
  let last: PointerEvent | null = null
  const paint = () => {
    frame = 0
    const e = last
    const card = (e?.target as Element | null)?.closest?.('[data-glow]') as HTMLElement | null
    if (!e || !card) return
    const r = card.getBoundingClientRect()
    const zoom = r.width ? r.width / card.offsetWidth : 1   // page zoom: pixels on screen vs pixels in the layout
    card.style.setProperty('--gx', `${(e.clientX - r.left) / zoom}px`)
    card.style.setProperty('--gy', `${(e.clientY - r.top) / zoom}px`)
  }
  const move = (e: PointerEvent) => { if (e.pointerType === 'touch') return; last = e; if (!frame) frame = requestAnimationFrame(paint) }
  document.addEventListener('pointermove', move, { passive: true })
  return () => { document.removeEventListener('pointermove', move); if (frame) cancelAnimationFrame(frame) }
}
