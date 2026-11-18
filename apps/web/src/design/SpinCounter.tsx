import { useEffect, useRef } from 'react'
import { cn } from './cn'
import { rollReel, showReel } from './spinReel'

/**
 * A number whose digits roll on a drum to the new value and land with a bounce. For a figure that
 * changes because you did something (a filter, a search) and should read as an event; for counts that tick often, use SubtleNumber.
 * The drums are drawn by script, so the text is exposed through aria-label.
 */
export function SpinCounter({ value, className }: { value: number; className?: string }) {
  const box = useRef<HTMLSpanElement>(null)
  const last = useRef<number | null>(null)
  const stop = useRef<(() => void) | null>(null)
  useEffect(() => {
    const el = box.current
    if (!el) return
    stop.current?.()
    stop.current = null
    const v = Math.max(0, Math.round(Number.isFinite(value) ? value : 0))
    if (last.current === null || last.current === v) showReel(el, v)
    else {
      // Hold the old width (digits right-aligned) while the drums roll, then ease to the new width: a number that loses a digit must not
      // pull the words next to it left in one jump.
      el.style.transition = 'none'
      el.style.minWidth = `${el.getBoundingClientRect().width}px`
      el.style.justifyContent = 'flex-end'
      stop.current = rollReel(el, last.current, v, () => {
        el.style.transition = 'min-width 240ms cubic-bezier(0.22, 1, 0.36, 1)'
        el.style.minWidth = '0px'
        el.style.justifyContent = ''
      })
    }
    last.current = v
  }, [value])
  useEffect(() => () => stop.current?.(), [])
  return <span ref={box} role="img" className={cn('mo-reel', className)} aria-label={Math.max(0, Math.round(value || 0)).toLocaleString('en-US')} />
}
