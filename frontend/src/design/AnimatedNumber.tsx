import { animate, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'

/** A number that counts to its new value instead of jumping (the first value is shown as is). */
export function AnimatedNumber({ value, format = (n: number) => n.toLocaleString() }: { value: number; format?: (n: number) => string }) {
  const reduce = useReducedMotion()
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  useEffect(() => {
    if (reduce || from.current === value || !Number.isFinite(value)) { from.current = value; setShown(value); return }
    const controls = animate(from.current, value, { duration: 0.45, ease: [0.2, 0.75, 0.25, 1], onUpdate: (n) => setShown(Math.round(n)), onComplete: () => { from.current = value; setShown(value) } })
    return () => { from.current = shown; controls.stop() }
  }, [value]) // eslint-disable-line react-hooks/exhaustive-deps
  return <span className="tabular-nums">{format(shown)}</span>
}
