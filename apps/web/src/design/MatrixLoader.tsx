import { cn } from './cn'

const CORNERS = [0, 3, 12, 15]
const RING = [1, 2, 7, 11, 14, 13, 8, 4]
const INNER = [5, 6, 9, 10]
const TWINKLE = [7, 2, 11, 5, 14, 9, 0, 12, 3, 15, 6, 10, 13, 1, 8, 4]
const CYCLE = 1200

export type MatrixVariant = 'scan' | 'twinkle' | 'orbit' | 'pulse'

/** Per-dot delays (ms) for each pattern: the variant is just a delay table (Transitions.dev "Matrix dot loader"). `null` means the dot holds still. */
export function dotDelays(variant: MatrixVariant, rounded = false, cycle = CYCLE): Array<number | null | 'gap'> {
  return Array.from({ length: 16 }, (_, i) => {
    if (rounded && CORNERS.includes(i)) return 'gap'
    if (variant === 'scan') return Math.round((i % 4) * (cycle / 10))
    if (variant === 'twinkle') return Math.round(TWINKLE[i]! * (cycle / 16))
    if (variant === 'orbit') { const k = RING.indexOf(i); return k === -1 ? null : Math.round(k * (cycle / 8)) }
    return INNER.includes(i) ? 0 : Math.round(cycle * 0.16)
  })
}

/** A small 4 by 4 dot loader that whispers where a spinner would shout. */
export function MatrixLoader({ variant = 'scan', rounded = false, dot = 3, label = 'Working', className }: { variant?: MatrixVariant; rounded?: boolean; dot?: number; label?: string; className?: string }) {
  return (
    <span role="status" aria-label={label} className={cn('t-matrix', className)} style={{ '--matrix-dot': `${dot}px` } as React.CSSProperties}>
      {dotDelays(variant, rounded).map((d, i) => d === 'gap' ? <i key={i} className="is-gap" /> : <i key={i} style={d === null ? { animation: 'none' } : ({ '--d': d } as React.CSSProperties)} />)}
    </span>
  )
}
