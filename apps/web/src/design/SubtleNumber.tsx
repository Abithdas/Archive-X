import { useEffect, useState } from 'react'
import { cn } from './cn'
import { diffDigits } from './subtleNumber'

const SHOW_MS = 520   // a little longer than --subtle-dur, then the number goes back to plain text

/**
 * A number whose changed digits cross-fade in place, rising when the value goes up and falling when it goes down (Transitions.dev
 * "Number subtle change"). Meant for numbers that update often, such as counts, where a bigger animation would turn into noise.
 */
export function SubtleNumber({ value, format = (n: number) => n.toLocaleString('en-US'), className }: { value: number; format?: (n: number) => string; className?: string }) {
  const text = format(value)
  const [shown, setShown] = useState({ text, value, prev: null as string | null, dir: 1, n: 0 })
  if (shown.text !== text) setShown({ text, value, prev: shown.text, dir: value >= shown.value ? 1 : -1, n: shown.n + 1 })   // derived while rendering, so the new digits never flash first
  useEffect(() => {
    if (shown.prev === null) return
    const t = setTimeout(() => setShown((s) => (s.n === shown.n ? { ...s, prev: null } : s)), SHOW_MS)
    return () => clearTimeout(t)
  }, [shown.n, shown.prev])
  if (shown.prev === null) return <span className={cn('t-subtle-number', className)}>{text}</span>
  return (
    <span key={shown.n} className={cn('t-subtle-number is-animating', className)} style={{ '--subtle-dir': shown.dir } as React.CSSProperties}>
      {diffDigits(shown.prev, text).map((p, i) => p.changed ? (
        <span key={i} className="t-subtle-slot">
          {p.old !== undefined ? <span className="t-subtle-old" aria-hidden="true">{p.old}</span> : null}
          <span className="t-subtle-new">{p.ch}</span>
        </span>
      ) : p.ch)}
    </span>
  )
}
