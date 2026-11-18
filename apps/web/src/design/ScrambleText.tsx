import { useEffect, useRef, useState } from 'react'
import { prefersReduced } from './motionPref'
import { scrambleFrame } from './scramble'

const DURATION = 620   // ms for the whole word to settle
const STEP = 38        // ms between glyph shuffles: quick enough to look alive, slow enough to read as decoding

/**
 * Text that decodes into its new value when it changes: random glyphs shuffle and the letters settle left to right.
 * The first value shows at once; so does every value when the person asked for less motion. Screen readers always get the real text.
 */
export function ScrambleText({ text, className }: { text: string; className?: string }) {
  const [shown, setShown] = useState(text)
  const previous = useRef(text)
  useEffect(() => {
    if (previous.current === text) return
    previous.current = text
    if (prefersReduced()) { setShown(text); return }
    const start = performance.now()
    let last = 0
    let frame = 0
    const tick = (now: number) => {
      const t = (now - start) / DURATION
      if (t >= 1) { setShown(text); return }
      if (now - last >= STEP) { last = now; setShown(scrambleFrame(text, t)) }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [text])
  const settled = shown === text
  return (
    <span className={className} aria-label={text}>
      <span aria-hidden="true" className={settled ? undefined : 'whitespace-nowrap'}>{settled ? text : shown}</span>
    </span>
  )
}
