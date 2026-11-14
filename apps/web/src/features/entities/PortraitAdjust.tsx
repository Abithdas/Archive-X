import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Dialog } from '@/design/Dialog'
import { DEFAULT_CROP, ZOOM_MAX, ZOOM_MIN, dragCrop, imageStyle, normalizeCrop, readCrop, startDrag, withZoom, writeCrop, type Crop } from './portrait'

const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5'

/** "Adjust framing": drag the photo and set the zoom. Saved in this browser for this performer only; the image itself is not changed. */
export function PortraitAdjust({ id, name, src, open, onClose, onSaved }: { id: number; name: string; src: string; open: boolean; onClose: () => void; onSaved: (c: Crop) => void }) {
  const [crop, setCrop] = useState<Crop>(DEFAULT_CROP)
  const [status, setStatus] = useState('')
  const frame = useRef<HTMLDivElement>(null)
  const drag = useRef<{ pointer: number; x: number; y: number; from: Crop } | null>(null)
  useEffect(() => { if (open) { setCrop(readCrop(id)); setStatus('') } }, [open, id])

  const begin = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    const from = startDrag(crop)
    drag.current = { pointer: e.pointerId, x: e.clientX, y: e.clientY, from }
    setCrop(from)
    try { frame.current?.setPointerCapture(e.pointerId) } catch { /* not capturable */ }
    e.preventDefault()
  }
  const move = (e: React.PointerEvent) => {
    const d = drag.current, box = frame.current?.getBoundingClientRect()
    if (!d || d.pointer !== e.pointerId || !box) return
    setCrop(dragCrop(d.from, e.clientX - d.x, e.clientY - d.y, box.width, box.height))
  }
  const end = (e: React.PointerEvent) => {
    if (drag.current?.pointer !== e.pointerId) return
    drag.current = null
    setStatus('Preview updated. Save to keep this framing.')
  }
  const save = () => {
    if (!writeCrop(id, crop)) return void setStatus('Could not save in this browser. Check its local storage settings and try again.')
    onSaved(normalizeCrop(crop))
    toast('Portrait framing saved for this performer.')
    onClose()
  }
  return (
    <Dialog open={open} onClose={onClose} title="Adjust portrait"
      footer={
        <div className="flex items-center gap-2">
          <button type="button" className={BTN} onClick={() => { setCrop(DEFAULT_CROP); setStatus('Original framing restored. Save to keep this setting.') }}>Reset</button>
          <span className="flex-1" />
          <button type="button" className={BTN} onClick={onClose}>Cancel</button>
          <button type="button" className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg hover:brightness-110" onClick={save}>Save framing</button>
        </div>
      }>
      <p className="mb-3 text-sm text-muted">{name || 'Set the framing for this performer.'}</p>
      <div ref={frame} aria-label="Portrait crop preview" onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
        className="relative mx-auto aspect-[3/4] w-[min(260px,70vw)] cursor-grab touch-none select-none overflow-hidden rounded-[20px] border border-line-2 bg-surface-2 active:cursor-grabbing">
        <img src={src} alt="Portrait preview" draggable={false} style={imageStyle(crop)} className="pointer-events-none size-full object-cover" />
        <span aria-hidden="true" className="pointer-events-none absolute inset-2 rounded-[14px] border border-white/30" />
      </div>
      <label className="mt-4 flex items-center justify-between text-sm font-semibold" htmlFor="portrait-zoom"><span>Zoom</span><output>{Math.round(crop.zoom * 100)}%</output></label>
      <input id="portrait-zoom" type="range" min={ZOOM_MIN} max={ZOOM_MAX} step={0.01} value={crop.zoom} aria-label="Portrait zoom"
        onChange={(e) => { setCrop(withZoom(crop, Number(e.target.value))); setStatus('Preview updated. Save to keep this framing.') }} className="mt-1 w-full accent-[var(--color-accent)]" />
      <p className="mt-2 text-xs text-muted">Drag the photo to set the framing.</p>
      <p role="status" aria-live="polite" className="mt-1 min-h-4 text-xs text-muted">{status}</p>
    </Dialog>
  )
}
