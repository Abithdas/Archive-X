/*
 * A text box with the two motions of the header search box, usable anywhere a search or filter box is needed:
 *  - Typing: each new letter springs in and a caret glides between letters (the idea of unlumen UI's "Animated Input", written from its
 *    public description, not from its paid source).
 *  - Clearing (x): the text sinks and fades, the placeholder drops in, and a light sweeps across (see clearDissolve.ts).
 * The real <input> keeps the text, the selection and the keyboard; the letters you see are drawn on top of it.
 */
import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type InputHTMLAttributes, type ReactNode, type Ref, type RefObject } from 'react'
import { startClear } from './clearDissolve'
import { Icon } from './Icon'
import { cn } from './cn'
import { spring } from './motion'

/** The font of an element as a canvas font string, built from its separate properties. The `font` shorthand cannot be used: browsers return it empty
 *  when any related property is not at its default (this box turns kerning off), and the canvas then silently measures in its own 10 px font, which
 *  put the caret at about 62% of the way along the text. */
export function fontOf(cs: Pick<CSSStyleDeclaration, 'fontStyle' | 'fontWeight' | 'fontSize' | 'fontFamily'>): string {
  return `${cs.fontStyle || 'normal'} ${cs.fontWeight || '400'} ${cs.fontSize || '16px'} ${cs.fontFamily || 'sans-serif'}`
}

/** Width of text in the input's own font, the way the browser lays it out (kerning off, to match the mirror's letter-by-letter boxes). */
function makeMeasure(input: () => HTMLInputElement | null) {
  let ctx: CanvasRenderingContext2D | null | undefined   // undefined: not asked yet; null: no canvas here
  return (s: string) => {
    const el = input()
    try {
      ctx ??= document.createElement('canvas').getContext('2d')
      if (ctx && el) {
        ctx.font = fontOf(getComputedStyle(el))
        ;(ctx as CanvasRenderingContext2D & { fontKerning?: string }).fontKerning = 'none'
        return ctx.measureText(s).width
      }
    } catch { /* no canvas (a test environment) */ }
    return s.length * 8
  }
}

export interface ClearHandle { clear: () => void }

export interface ClearFieldProps {
  value: string
  onChange: (v: string) => void
  placeholder: string
  /** The input's accessible name. */
  label: string
  clearLabel?: string
  icon?: 'search' | null
  /** Shown at the right while the box is empty (a shortcut hint, say). */
  trailing?: ReactNode
  inputRef?: RefObject<HTMLInputElement | null>
  /** Extra attributes for the input: id, role, aria-*, autoFocus, onFocus, onKeyDown ... */
  inputProps?: Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'placeholder' | 'className' | 'style' | 'ref'>
  /** Called as the dissolve starts (a list under the box closes, say). */
  onClear?: () => void
  handle?: Ref<ClearHandle>
  /** Size, border and surface of the box. */
  className?: string
}

export function ClearField({ value, onChange, placeholder, label, clearLabel = 'Clear', icon = 'search', trailing, inputRef, inputProps, onClear, handle, className }: ClearFieldProps) {
  const reduce = Boolean(useReducedMotion())
  const own = useRef<HTMLInputElement | null>(null)
  const ref = inputRef ?? own
  const wrap = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLDivElement>(null)
  const ghost = useRef<HTMLDivElement>(null)
  const phold = useRef<HTMLDivElement>(null)
  const glow = useRef<HTMLDivElement>(null)
  const stop = useRef<(() => void) | null>(null)
  const measureRef = useRef(makeMeasure(() => ref.current))
  const [clearing, setClearing] = useState(false)
  const [fresh, setFresh] = useState(0)      // how many letters at the end were just typed (they spring in)
  const [caret, setCaret] = useState({ x: 0, show: false })
  const [scroll, setScroll] = useState(0)

  const syncCaret = useCallback(() => {
    const el = ref.current
    if (!el) return
    const at = el.selectionStart ?? el.value.length
    const end = el.selectionEnd ?? at
    setScroll(el.scrollLeft)
    setCaret({ x: measureRef.current(el.value.slice(0, at)) - el.scrollLeft, show: at === end && document.activeElement === el })
  }, [ref])
  const later = useCallback(() => { requestAnimationFrame(syncCaret) }, [syncCaret])
  useLayoutEffect(syncCaret, [value, syncCaret])
  useEffect(() => () => stop.current?.(), [])

  const change = (next: string) => {
    const grew = next.length - value.length
    setFresh(grew > 0 ? grew : 0)
    onChange(next)
  }

  const clear = () => {
    const el = ref.current
    if (clearing || !value) return
    onClear?.()
    if (reduce || !ghost.current || !phold.current || !glow.current || !wrap.current || !field.current) { onChange(''); return }
    const keepFocus = document.activeElement === el
    const text = value
    setClearing(true)
    setFresh(0)
    onChange('')
    stop.current = startClear({ ghost: ghost.current, placeholder: phold.current, glow: glow.current }, text, {
      onDone: () => {
        stop.current = null
        setClearing(false)
        if (keepFocus) requestAnimationFrame(() => el?.focus({ preventScroll: true }))
      },
    })
  }
  useImperativeHandle(handle, () => ({ clear }))

  const chars = Array.from(value)
  const firstFresh = Math.max(0, chars.length - fresh)

  return (
    <div ref={wrap} className={cn('mo-clear ax-search flex items-center gap-2.5 text-faint focus-within:border-accent/50', value && 'has-value', clearing && 'is-clearing', className)}>
      {icon ? <Icon name={icon} className="shrink-0" /> : null}
      <div ref={field} className="ax-field relative h-full min-w-0 flex-1 text-base text-fg">
        <input
          type="text"
          autoComplete="off"
          spellCheck={false}
          {...inputProps}
          ref={ref}
          aria-label={label}
          value={value}
          onChange={(e) => change(e.target.value)}
          onFocus={(e) => { inputProps?.onFocus?.(e); later() }}
          onBlur={(e) => { inputProps?.onBlur?.(e); later() }}
          onKeyUp={later}
          onSelect={later}
          onClick={later}
          onScroll={later}
          onInput={later}
        />
        <div className="ax-live" aria-hidden="true">
          <span className="ax-live-inner" style={{ transform: `translateX(${-scroll}px)` }}>
            {chars.map((c, i) => <span key={i} className={cn('ax-char', i >= firstFresh && 'ax-char-in')} style={{ '--i': Math.min(i - firstFresh, 12) } as React.CSSProperties}>{c}</span>)}
          </span>
        </div>
        <div ref={ghost} className="mo-clear-mirror" aria-hidden="true" />
        <div ref={phold} className="mo-clear-placeholder text-faint" aria-hidden="true">{placeholder}</div>
        <motion.span aria-hidden="true" className="ax-caret" data-show={caret.show && !clearing} animate={{ x: caret.x }} initial={false} transition={reduce ? { duration: 0 } : spring} />
      </div>
      {value ? (
        <button
          type="button"
          className="t-clear-btn grid size-7 shrink-0 place-items-center rounded-lg text-muted hover:bg-fg/5 hover:text-fg"
          aria-label={clearLabel}
          onPointerDown={(e) => { if (document.activeElement === ref.current) e.preventDefault() }}
          onClick={clear}
        >
          <Icon name="close" className="size-4" />
        </button>
      ) : trailing}
      <div ref={glow} className="mo-clear-glow" aria-hidden="true" />
    </div>
  )
}
