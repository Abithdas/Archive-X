import { useRef, useState } from 'react'
import { cn } from '@/design/cn'
import { formatDuration } from '@/features/library/format'
import { frameIndexFor, keyStep, segmentAt, timeAt, type Segment } from './timeline'

export interface PreviewInfo { url: (frame: number) => string; times: number[] }
const clock = (s: number) => formatDuration(s) || '0:00'

/**
 * Seek bar: buffered + played, chapter segments, a hover/scrub tooltip with the nearest preview frame, the time and the
 * scene title. Dragging scrubs and seeks on release (no flood of range requests while moving); arrow keys, PageUp/Down,
 * Home and End work when focused.
 */
export function Timeline({ time, duration, buffered, segments, preview, onSeek }: { time: number; duration: number; buffered: number; segments: Segment[]; preview?: PreviewInfo; onSeek: (t: number) => void }) {
  const track = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ t: number; x: number } | null>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const [imgOk, setImgOk] = useState(true)

  const read = (e: { clientX: number }) => {
    const r = track.current!.getBoundingClientRect()
    return { t: timeAt(e.clientX, r.left, r.width, duration), x: Math.max(0, Math.min(r.width, e.clientX - r.left)), w: r.width }
  }
  const shown = drag ?? time
  const pct = (t: number) => (duration > 0 ? Math.min(100, (t / duration) * 100) : 0)
  const tip = hover
  const seg = tip ? segmentAt(segments, tip.t) : undefined
  const frame = preview && tip ? frameIndexFor(preview.times, tip.t) : -1

  return (
    <div
      ref={track}
      role="slider"
      tabIndex={duration ? 0 : -1}
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={Math.floor(duration)}
      aria-valuenow={Math.floor(shown)}
      aria-valuetext={`${clock(shown)} of ${duration ? clock(duration) : 'unknown'}`}
      aria-disabled={!duration}
      onKeyDown={(e) => {
        const n = keyStep(e.key, time, duration)
        if (n !== null && duration) { e.preventDefault(); e.stopPropagation(); onSeek(n) }
      }}
      onPointerDown={(e) => {
        if (!duration) return
        e.currentTarget.setPointerCapture(e.pointerId)
        const p = read(e)
        setDrag(p.t)
        setHover({ t: p.t, x: p.x })
      }}
      onPointerMove={(e) => {
        if (!duration) return
        const p = read(e)
        setHover({ t: p.t, x: p.x })
        if (drag !== null) setDrag(p.t)
      }}
      onPointerUp={(e) => {
        if (drag === null) return
        const p = read(e)
        setDrag(null)
        onSeek(p.t)
        if (e.pointerType !== 'mouse') setHover(null)
      }}
      onPointerCancel={() => { setDrag(null); setHover(null) }}
      onPointerLeave={() => drag === null && setHover(null)}
      className="group relative h-7 w-full cursor-pointer touch-none select-none rounded outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
    >
      {tip && duration ? (
        <div className="pointer-events-none absolute bottom-full z-20 mb-2 grid w-max max-w-[220px] -translate-x-1/2 justify-items-center gap-1" style={{ left: `clamp(${preview ? 82 : 28}px, ${tip.x}px, calc(100% - ${preview ? 82 : 28}px))` }}>
          {frame >= 0 && imgOk ? (
            <img src={preview!.url(frame)} alt="" width={160} height={90} onError={() => setImgOk(false)} className="h-[90px] w-[160px] rounded-lg border border-line-2 bg-black object-cover shadow-[0_8px_24px_rgb(0_0_0/0.5)]" />
          ) : null}
          {seg ? <span className="max-w-[200px] truncate rounded-md bg-black/75 px-2 py-0.5 text-[11px] font-semibold text-white">{seg.title}</span> : null}
          <span className="rounded-md bg-black/80 px-2 py-0.5 text-xs font-bold tabular-nums text-white">{clock(tip.t)}</span>
        </div>
      ) : null}
      <div className={cn('absolute inset-x-0 top-1/2 -translate-y-1/2 transition-[height] duration-150', tip || drag !== null ? 'h-2.5' : 'h-1.5')}>
        <div className="absolute inset-0 overflow-hidden rounded-full bg-fg/15">
          <span className="absolute inset-y-0 left-0 bg-fg/20" style={{ width: `${pct(buffered)}%` }} />
          <span className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${pct(shown)}%` }} />
        </div>
        {segments.map((s, i) => (
          <span key={i} aria-hidden="true" data-segment={s.title} className="absolute inset-y-0 border-r-2 border-bg last:border-r-0" style={{ left: `${pct(s.start)}%`, width: `${pct(s.end) - pct(s.start)}%` }} />
        ))}
        <span aria-hidden="true" className={cn('absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent shadow transition-transform', tip || drag !== null ? 'scale-100' : 'scale-0 group-focus-visible:scale-100')} style={{ left: `${pct(shown)}%` }} />
      </div>
    </div>
  )
}
