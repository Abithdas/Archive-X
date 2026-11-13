import { useRef, type KeyboardEvent, type PointerEvent } from 'react'
import { cn } from '@/design/cn'
import { clampPos, type CaptionStyle } from './captionStyle'

/** The picture's captions drawn as subtitles: outlined text, no box unless chosen. Drag them (or use the arrow keys) to move them; size and background are in Settings. */
export function CaptionOverlay({ text, style, onMove, onMoveEnd }: { text: string; style: CaptionStyle; onMove: (x: number, y: number) => void; onMoveEnd: () => void }) {
  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const frame = (el: HTMLElement) => el.closest<HTMLElement>('[data-caption-frame]')
  const down = (e: PointerEvent<HTMLDivElement>) => {
    const f = frame(e.currentTarget); if (!f) return
    const r = f.getBoundingClientRect(); e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { dx: (e.clientX - r.left) / r.width - style.x, dy: (e.clientY - r.top) / r.height - style.y }
    e.stopPropagation()
  }
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current; const f = frame(e.currentTarget); if (!d || !f) return
    const r = f.getBoundingClientRect()
    onMove(clampPos((e.clientX - r.left) / r.width - d.dx), clampPos((e.clientY - r.top) / r.height - d.dy))
  }
  const up = () => { if (drag.current) { drag.current = null; onMoveEnd() } }
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 0.05 : 0.01
    const d = e.key === 'ArrowLeft' ? [-step, 0] : e.key === 'ArrowRight' ? [step, 0] : e.key === 'ArrowUp' ? [0, -step] : e.key === 'ArrowDown' ? [0, step] : null
    if (!d) return
    e.preventDefault(); e.stopPropagation()
    onMove(clampPos(style.x + d[0]!), clampPos(style.y + d[1]!)); onMoveEnd()
  }
  return (
    <div
      data-testid="caption" role="group" aria-label="Captions. Drag to move; arrow keys also move them." tabIndex={0}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onKeyDown={key} onClick={(e) => e.stopPropagation()}
      style={{ left: `${style.x * 100}%`, top: `${style.y * 100}%`, fontSize: `calc(${style.size} * clamp(13px, 2.6cqw, 36px))` }}
      className={cn(
        'absolute z-10 w-max max-w-[88%] -translate-x-1/2 -translate-y-1/2 cursor-move touch-none select-none whitespace-pre-line text-center font-semibold leading-snug text-white outline-none focus-visible:ring-2 focus-visible:ring-accent',
        '[paint-order:stroke_fill] [-webkit-text-stroke:0.14em_rgb(0_0_0/0.92)] [text-shadow:0_0.06em_0.18em_rgb(0_0_0/0.85)]',
        style.bg === 'soft' && 'rounded-md bg-black/45 px-[0.5em] py-[0.15em]',
        style.bg === 'solid' && 'rounded-md bg-black px-[0.5em] py-[0.15em] [-webkit-text-stroke:0]',
      )}
    >
      {text}
    </div>
  )
}
