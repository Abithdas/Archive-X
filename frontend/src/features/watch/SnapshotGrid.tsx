import { useEffect, useRef, useState } from 'react'
import { cn } from '@/design/cn'
import { formatDuration } from '@/features/library/format'
import { layoutFor, showsLabel, spriteTile, tileLabel } from './grid'

export interface SnapshotData { times: number[]; spriteUrl: string | null; frameUrl: (index: number) => string; aspect: number }

/**
 * The 36-frame contact sheet shown over the player before playback. Each tile is a real button: click (or Enter) starts
 * playback at that moment. One decoded sprite is used when the server has it, otherwise the individual frames.
 */
export function SnapshotGrid({ data, onPick }: { data: SnapshotData; onPick: (seconds: number) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [sprite, setSprite] = useState<{ w: number; h: number } | null>(null)
  const [spriteFailed, setSpriteFailed] = useState(false)

  useEffect(() => {
    const el = box.current
    if (!el) return
    const read = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    setSprite(null); setSpriteFailed(false)
    if (!data.spriteUrl) return
    let live = true
    const img = new Image()
    img.decoding = 'async'
    img.onload = () => { if (live && img.naturalWidth > 0) setSprite({ w: img.naturalWidth, h: img.naturalHeight }); else if (live) setSpriteFailed(true) }
    img.onerror = () => { if (live) setSpriteFailed(true) }
    img.src = data.spriteUrl
    return () => { live = false; img.onload = null; img.onerror = null }
  }, [data.spriteUrl])

  const useSprite = Boolean(data.spriteUrl) && !spriteFailed
  const ready = size.w > 0 && (!useSprite || sprite !== null)   // appear only once there is something real to show
  const { rows, cols } = layoutFor(size.w, size.h, data.aspect)
  const tileW = size.w / cols, tileH = size.h / rows

  return (
    <div ref={box} role="group" aria-label="Video snapshots. Choose one to start playing from that moment." className={cn('absolute inset-0 z-[5] grid bg-[#101010] transition-opacity duration-200', ready ? 'opacity-100' : 'pointer-events-none opacity-0')} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
      {data.times.map((t, i) => {
        const label = formatDuration(t) || '0:00'
        return (
          <button key={i} type="button" onClick={() => onPick(t)} aria-label={tileLabel(t)} title={tileLabel(t)} tabIndex={ready ? 0 : -1}
            className="group relative min-h-0 min-w-0 overflow-hidden outline-none focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
            style={useSprite && sprite ? { backgroundImage: `url(${data.spriteUrl})`, backgroundRepeat: 'no-repeat', ...spriteTile(i, tileW, tileH, sprite.w, sprite.h) } : undefined}>
            {!useSprite ? <img src={data.frameUrl(i)} alt="" loading="lazy" decoding="async" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} className="absolute inset-0 size-full object-cover" /> : null}
            <span className="absolute inset-0 bg-black/0 transition-colors group-hover:bg-black/20" />
            {showsLabel(tileW, tileH, label) ? <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[11px] font-semibold leading-[15px] text-white">{label}</span> : null}
          </button>
        )
      })}
    </div>
  )
}
