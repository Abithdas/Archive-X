import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/design/cn'
import { SLIDE_MS, SWIPE_PX, clampPan, counterText, favKey, fitScale, hud, photoName, rotate, stepIndex, stripRange, zoomBy, zoomPercent, type Photo } from './photos'

const PILL = 'grid h-9 min-w-9 place-items-center rounded-full px-2.5 text-base font-semibold text-white hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent aria-pressed:bg-white/20'

/** Full-screen photo viewer: preview first, original swapped in when ready; zoom, pan, rotate, slideshow, filmstrip, keyboard. */
export function PhotoViewer({ items, index, total, favorites, onIndex, onClose, onFavorite, onNeedMore }: {
  items: Photo[]; index: number; total: number; favorites: ReadonlySet<string>
  onIndex: (i: number) => void; onClose: () => void; onFavorite: (p: Photo) => void; onNeedMore: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const strip = useRef<HTMLDivElement>(null)
  const item = items[index]
  const [box, setBox] = useState({ w: 0, h: 0 })
  const [nat, setNat] = useState({ w: 0, h: 0 })
  const [mul, setMul] = useState(1)   // magnification relative to the fitted size
  const [rot, setRot] = useState(0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [playing, setPlaying] = useState(false)
  const [fs, setFs] = useState(false)
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const drag = useRef<null | { x: number; y: number; px: number; py: number; moved: boolean }>(null)

  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
  }, [])
  useEffect(() => {
    const el = stage.current
    if (!el) return
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight })
    measure()
    if (typeof ResizeObserver !== 'function') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const key = item ? `${item.channel_id}:${item.message_id}` : ''
  // A new photo starts fitted, unrotated, showing its preview; the original replaces it once decoded.
  useEffect(() => {
    setMul(1); setRot(0); setPan({ x: 0, y: 0 }); setOriginal(''); setLoading(true)
    if (!item) return
    setNat({ w: item.width, h: item.height })
    let live = true
    const img = new Image()
    img.onload = () => { if (live) setOriginal(item.image_url) }
    img.src = item.image_url
    return () => { live = false; img.onload = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  useEffect(() => { if (items.length - index <= 6 && items.length < total) onNeedMore() }, [index, items.length, total, onNeedMore])

  const fit = fitScale(nat, box, rot)
  const scale = fit * mul
  const atFit = mul <= 1
  const go = useCallback((d: number, wrap = false) => { const t = stepIndex(index, d, items.length, wrap); if (t !== null) onIndex(t) }, [index, items.length, onIndex])
  const setScale = (s: number) => { const m = Math.max(1, s / fit); setMul(m); setPan((p) => clampPan(p.x, p.y, nat, box, fit * m, rot)) }
  const zoomStep = (dir: number) => setScale(zoomBy(scale, dir, fit))
  const doFit = () => { setMul(1); setPan({ x: 0, y: 0 }) }
  const turn = (deg: number) => { setRot((r) => rotate(r, deg)); setMul(1); setPan({ x: 0, y: 0 }) }
  const toggleFs = useCallback(() => {
    const d = ref.current
    if (!d) return
    if (document.fullscreenElement) void document.exitFullscreen?.()
    else void d.requestFullscreen?.().catch(() => undefined)
  }, [])
  useEffect(() => {
    const on = () => setFs(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', on)
    return () => document.removeEventListener('fullscreenchange', on)
  }, [])

  useEffect(() => {
    if (!playing) return
    const t = setInterval(() => go(1, true), SLIDE_MS)
    return () => clearInterval(t)
  }, [playing, go])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest?.('input,textarea,select,[contenteditable="true"]')) return
      const k = e.key
      const act = k === 'ArrowLeft' ? () => go(-1) : k === 'ArrowRight' ? () => go(1) : k === 'ArrowUp' || k === '+' || k === '=' ? () => zoomStep(1)
        : k === 'ArrowDown' || k === '-' || k === '_' ? () => zoomStep(-1) : k === '0' ? doFit : k === 'r' || k === 'R' ? () => turn(e.shiftKey ? -90 : 90)
        : k === 'f' || k === 'F' ? toggleFs : k === ' ' ? () => setPlaying((p) => !p) : null
      if (act) { e.preventDefault(); act() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  useEffect(() => {
    const cur = strip.current?.querySelector('[aria-current="true"]')
    ;(cur as HTMLElement | null)?.scrollIntoView?.({ block: 'nearest', inline: 'center' })
  }, [index])

  const down = (e: RPointerEvent) => { drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y, moved: false }; (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId) }
  const move = (e: RPointerEvent) => {
    const d = drag.current
    if (!d || atFit) return
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true
    setPan(clampPan(d.px + e.clientX - d.x, d.py + e.clientY - d.y, nat, box, scale, rot))
  }
  const up = (e: RPointerEvent) => {
    const d = drag.current
    drag.current = null
    if (d && atFit) { const dx = e.clientX - d.x; if (Math.abs(dx) >= SWIPE_PX) go(dx < 0 ? 1 : -1) }
  }
  const wheel = (e: React.WheelEvent) => { zoomStep(e.deltaY < 0 ? 1 : -1) }

  const h = useMemo(() => (item ? hud(item) : null), [item])
  const [from, to] = stripRange(index, items.length)
  const fav = item ? favorites.has(favKey(item)) : false
  if (!item || !h) return null

  return createPortal(
    <dialog ref={ref} aria-label="Photo viewer" onClose={onClose} onCancel={(e) => { e.preventDefault(); onClose() }} onClick={(e) => e.target === ref.current && onClose()}
      className="fixed inset-0 m-0 hidden h-[calc(100dvh/var(--ax-page-zoom,1))] max-h-none w-[calc(100vw/var(--ax-page-zoom,1))] max-w-none flex-col bg-black/95 p-0 text-white open:flex">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
          <b className="max-w-[40vw] truncate text-sm" title={h.title}>{h.title}</b>
          {[h.dims, h.mp, h.size, h.date].filter(Boolean).map((t) => <span key={t} className="rounded-full bg-white/10 px-2 py-0.5">{t}</span>)}
          {h.channel ? <span className="max-w-[30vw] truncate rounded-full bg-white/10 px-2 py-0.5" title={h.channel}>{h.channel}</span> : null}
        </div>
        <div className="flex items-center gap-2">
          <span aria-live="polite" className="text-xs text-white/70">{counterText(index, total || items.length)}</span>
          <button type="button" onClick={onClose} aria-label="Close photo viewer" title="Close (Esc)" className={PILL}>✕</button>
        </div>
      </div>

      <div ref={stage} role="img" aria-label={`${h.title}. Drag to pan, scroll or use arrow keys to zoom, swipe to navigate.`} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { drag.current = null }} onWheel={wheel} onDoubleClick={() => (atFit ? setScale(fit * 2.5) : doFit())}
        className={cn('relative min-h-0 flex-1 touch-none select-none overflow-hidden', atFit ? 'cursor-zoom-in' : 'cursor-grab active:cursor-grabbing')}>
        <img src={original || item.preview_url} alt={photoName(item)} draggable={false} onLoad={(e) => { setLoading(false); if (!item.width) setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight }) }} onError={() => setLoading(false)}
          style={{ width: nat.w || undefined, height: nat.h || undefined, maxWidth: 'none', transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) rotate(${rot}deg) scale(${scale})` }}
          className="absolute left-1/2 top-1/2 origin-center transition-transform duration-150 ease-out" />
        {loading ? <div role="status" aria-label="Loading selected photo" className="absolute inset-0 grid place-items-center"><span className="size-10 animate-spin rounded-full border-4 border-white/20 border-t-white" /></div> : null}
      </div>

      <div className="shrink-0 px-3 pb-3">
        <div role="toolbar" aria-label="Photo viewer controls" className="mx-auto mb-2 flex w-fit max-w-full flex-wrap items-center justify-center gap-0.5 rounded-full bg-white/10 px-2 py-1 backdrop-blur">
          <button type="button" className={PILL} onClick={() => go(-1)} disabled={index <= 0} title="Previous photo (←)" aria-label="Previous photo">‹</button>
          <button type="button" className={PILL} onClick={() => setPlaying(!playing)} aria-pressed={playing} title={playing ? 'Pause slideshow (Space)' : 'Play slideshow (Space)'} aria-label={playing ? 'Pause slideshow' : 'Play slideshow'}>{playing ? 'Ⅱ' : '▶'}</button>
          <button type="button" className={PILL} onClick={() => go(1)} disabled={index >= items.length - 1} title="Next photo (→)" aria-label="Next photo">›</button>
          <span aria-hidden="true" className="mx-1 h-5 w-px bg-white/25" />
          <button type="button" className={PILL} onClick={() => zoomStep(-1)} title="Zoom out (↓)" aria-label="Zoom out">↓</button>
          <span role="status" aria-live="polite" aria-label="Zoom level" className="min-w-12 text-center text-xs tabular-nums">{zoomPercent(scale, fit)}</span>
          <button type="button" className={PILL} onClick={() => zoomStep(1)} title="Zoom in (↑)" aria-label="Zoom in">↑</button>
          <button type="button" className={PILL} onClick={doFit} title="Fit full image (0)" aria-label="Fit image">Fit</button>
          <span aria-hidden="true" className="mx-1 h-5 w-px bg-white/25" />
          <button type="button" className={PILL} onClick={() => turn(-90)} title="Rotate counter-clockwise (Shift+R)" aria-label="Rotate counter-clockwise">↶</button>
          <button type="button" className={PILL} onClick={() => turn(90)} title="Rotate clockwise (R)" aria-label="Rotate clockwise">↷</button>
          <button type="button" className={cn(PILL, fav && 'text-warn')} onClick={() => onFavorite(item)} aria-pressed={fav} title={fav ? 'Remove from favourites' : 'Add to favourites'} aria-label={fav ? 'Remove from favourites' : 'Add to favourites'}>{fav ? '★' : '☆'}</button>
          <button type="button" className={PILL} onClick={toggleFs} aria-pressed={fs} title="Toggle fullscreen (F)" aria-label="Toggle fullscreen">⛶</button>
        </div>
        <div ref={strip} aria-label="Photo carousel" className="flex gap-1.5 overflow-x-auto pb-1">
          {items.slice(from, to).map((p, k) => {
            const i = from + k
            return (
              <button key={`${p.channel_id}:${p.message_id}`} type="button" onClick={() => onIndex(i)} aria-label={`Photo ${i + 1}: ${photoName(p)}`} aria-current={i === index ? 'true' : undefined}
                className={cn('relative h-14 w-20 shrink-0 overflow-hidden rounded-lg border-2 opacity-70 hover:opacity-100', i === index ? 'border-accent opacity-100' : 'border-transparent')}>
                <img src={p.thumbnail_url} alt="" loading="lazy" draggable={false} className="size-full object-cover" />
                {favorites.has(favKey(p)) ? <span aria-hidden="true" className="absolute right-0.5 top-0 text-xs text-warn">★</span> : null}
              </button>
            )
          })}
        </div>
      </div>
    </dialog>,
    document.body,
  )
}
