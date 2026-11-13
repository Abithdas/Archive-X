import { animate, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'

/** The value to show while counting from the previous one to `value` (the first value is shown as is; reduced motion jumps). */
export function useCountTo(value: number): number {
  const reduce = useReducedMotion()
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  useEffect(() => {
    if (reduce || from.current === value || !Number.isFinite(value)) { from.current = value; setShown(value); return }
    const controls = animate(from.current, value, { duration: 0.45, ease: [0.2, 0.75, 0.25, 1], onUpdate: (n) => setShown(Math.round(n)), onComplete: () => { from.current = value; setShown(value) } })
    return () => { from.current = shown; controls.stop() }
  }, [value]) // eslint-disable-line react-hooks/exhaustive-deps
  return shown
}

/** A number that counts to its new value instead of jumping. */
export function AnimatedNumber({ value, format = (n: number) => n.toLocaleString() }: { value: number; format?: (n: number) => string }) {
  return <span className="tabular-nums">{format(useCountTo(value))}</span>
}
