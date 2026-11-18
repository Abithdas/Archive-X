import { useEffect, useRef, useState } from 'react'

/** How long one ring lasts, the same as the animations in transitions.css. */
const RING_MS = 1100

/** The bell glyph in two parts, so the bell and the clapper inside it can swing on their own (the clapper later and the other way, like a real one). */
function BellGlyph() {
  return (
    <svg className="ax-icon" viewBox="0 0 256 256" aria-hidden="true" focusable="false">
      <g className="ax-bell-body">
        <path fill="currentColor" fillRule="evenodd" d="M221.8,175.94C216.25,166.38,208,139.33,208,104a80,80,0,1,0-160,0c0,35.34-8.26,62.38-13.81,71.94A16,16,0,0,0,48,200H208a16,16,0,0,0,13.8-24.06ZM48,184c7.7-13.24,16-43.92,16-80a64,64,0,1,1,128,0c0,36.05,8.28,66.73,16,80Z" />
        <g className="ax-bell-clapper"><path fill="currentColor" d="M96,212h64a32,32,0,0,1-64,0Z" /></g>
      </g>
    </svg>
  )
}

/** The bell and its count badge. The badge is a small round disc that slides on diagonally and pops.
 *  The bell rings when a new notice arrives and when the pointer is on it: the bell swings from its top and the clapper swings separately. The number
 *  stays visible while the badge closes. */
export function Bell({ count, className }: { count: number; className?: string }) {
  const shown = useRef(count)
  if (count > 0) shown.current = count
  const open = count > 0
  const before = useRef(count)
  const [ringing, setRinging] = useState(false)
  useEffect(() => {
    const more = count > before.current
    before.current = count
    if (!more) return
    setRinging(true)
    const t = setTimeout(() => setRinging(false), RING_MS)
    return () => clearTimeout(t)
  }, [count])
  return (
    <span className={`ax-bell relative grid place-items-center ${ringing ? 'is-ringing' : ''} ${className ?? ''}`}>
      <BellGlyph />
      <span className="mo-badge" data-open={open ? 'true' : 'false'} aria-hidden="true">
        <span className="mo-badge-dot">{shown.current > 99 ? '99+' : shown.current}</span>
      </span>
    </span>
  )
}
