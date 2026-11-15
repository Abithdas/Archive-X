import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ImgHTMLAttributes, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { flight, frames, targetBox, type Box } from './zoomGeometry'

const OPEN_MS = 560
const CLOSE_MS = 420
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)'
const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
const boxOf = (el: Element): Box => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height } }

/**
 * An image that opens bigger when you click it, for reviewing a small picture properly: it grows from where it is to half the window with a
 * 3D tilt that settles flat, the page dims behind it, and a second click (or Escape) shrinks it back to its place the same way.
 * Written for Archive X from the idea of Transitions.dev's "Image open tilt" (a paid transition whose code is not used here).
 * `fullSrc` is the bigger picture to show once open, when the thumbnail is a small version.
 */
export function ZoomImage({ fullSrc, className, onClick, onKeyDown, ...img }: ImgHTMLAttributes<HTMLImageElement> & { src: string; fullSrc?: string }) {
  const thumb = useRef<HTMLImageElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const backdrop = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [origin, setOrigin] = useState<Box | null>(null)
  const closing = useRef(false)
  const label = img.alt ? `Enlarge: ${img.alt}` : 'Enlarge image'

  const show = useCallback(() => {
    const el = thumb.current
    if (!el || open) return
    setOrigin(boxOf(el)); closing.current = false; setOpen(true)
  }, [open])

  const target = (): Box => {
    const el = thumb.current
    const natural = el && el.naturalWidth && el.naturalHeight ? el.naturalWidth / el.naturalHeight : origin ? origin.width / origin.height : 16 / 9
    return targetBox(window.innerWidth, window.innerHeight, natural)
  }

  // Opening: place the stage at its final box, then fly it in from the thumbnail.
  useLayoutEffect(() => {
    if (!open || !origin || !stage.current) return
    const st = stage.current
    const to = target()
    Object.assign(st.style, { left: `${to.left}px`, top: `${to.top}px`, width: `${to.width}px`, height: `${to.height}px` })
    const f = flight(origin, to, window.innerWidth, window.innerHeight)
    if (reduced() || typeof st.animate !== 'function') { if (backdrop.current) backdrop.current.style.opacity = '1'; return }
    st.animate(frames(f, 14).map((k) => ({ transform: k.transform, clipPath: k.clipPath, offset: k.offset })), { duration: OPEN_MS, easing: EASE, fill: 'both' })
    backdrop.current?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: OPEN_MS * 0.7, easing: 'ease-out', fill: 'both' })
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const hide = useCallback(() => {
    const st = stage.current
    if (!open || closing.current) return
    closing.current = true
    const finish = () => { setOpen(false); closing.current = false; thumb.current?.focus({ preventScroll: true }) }
    const el = thumb.current
    if (!st || !el || reduced() || typeof st.animate !== 'function') return finish()
    const from = boxOf(el)      // measured again: the page may have moved while the image was open
    const f = flight(from, target(), window.innerWidth, window.innerHeight)
    const back = frames(f, 14).reverse().map((k) => ({ transform: k.transform, clipPath: k.clipPath, offset: 1 - k.offset }))
    const a = st.animate(back, { duration: CLOSE_MS, easing: EASE, fill: 'both' })
    backdrop.current?.animate([{ opacity: 1 }, { opacity: 0 }], { duration: CLOSE_MS * 0.9, easing: 'ease-in', fill: 'both' })
    a.onfinish = finish
    a.oncancel = finish
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return
    const key = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); hide() } }
    document.addEventListener('keydown', key, true)
    window.addEventListener('resize', hide)
    return () => { document.removeEventListener('keydown', key, true); window.removeEventListener('resize', hide) }
  }, [open, hide])

  return (
    <>
      <img
        {...img}
        ref={thumb}
        data-zoomable=""
        role="button"
        tabIndex={0}
        aria-label={label}
        aria-expanded={open}
        className={className}
        style={{ ...img.style, ...(open ? { visibility: 'hidden' } : null) }}
        onClick={(e) => { onClick?.(e); if (!e.defaultPrevented) show() }}
        onKeyDown={(e: KeyboardEvent<HTMLImageElement>) => { onKeyDown?.(e); if ((e.key === 'Enter' || e.key === ' ') && !e.defaultPrevented) { e.preventDefault(); show() } }}
      />
      {open ? createPortal(
        <div className="ax-zoom-root" role="dialog" aria-modal="true" aria-label={img.alt ? `Enlarged: ${img.alt}` : 'Enlarged image'} onClick={hide}>
          <div ref={backdrop} className="ax-zoom-backdrop" />
          <div ref={stage} className="ax-zoom-stage"><img src={fullSrc || thumb.current?.currentSrc || img.src} alt={img.alt ?? ''} referrerPolicy={img.referrerPolicy} draggable={false} /></div>
        </div>, document.body) : null}
    </>
  )
}
