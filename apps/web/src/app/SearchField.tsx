/*
 * The header search box.
 *  - Typing: each new letter springs in, and a caret glides between letters (the idea of unlumen UI's "Animated Input"; written here from
 *    its public description, not from its paid source).
 *  - Clearing (×): the text dissolves (Transitions.dev "Input clear with dissolve", see design/clearDissolve.ts).
 *  - Suggestions: a keyboard-driven combobox that lists matching performers and studios with their picture, like a favicon search
 *    (after SmoothUI's favicon-search, MIT, Copyright (c) 2024 Eduardo Calvo).
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { startClear } from '@/design/clearDissolve'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { spring } from '@/design/motion'
import { highlightParts, type Suggestion } from './searchSuggest'

function Highlight({ text, query }: { text: string; query: string }) {
  return <>{highlightParts(text, query).map((p, i) => (p.match ? <mark key={i} className="rounded-sm bg-accent/25 text-fg">{p.text}</mark> : <span key={i}>{p.text}</span>))}</>
}

function Tile({ s }: { s: Suggestion }) {
  const [broken, setBroken] = useState(false)
  if (broken) return <span aria-hidden="true" className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-3 text-xs font-bold uppercase text-muted">{s.title.charAt(0)}</span>
  return <img src={s.image} alt="" aria-hidden="true" loading="lazy" onError={() => setBroken(true)} className="size-8 shrink-0 rounded-lg bg-surface-3 object-cover" />
}

/** Width of text in the input's own font, the way the browser lays it out (kerning off, to match the mirror's letter-by-letter boxes). */
function makeMeasure(input: () => HTMLInputElement | null) {
  let ctx: CanvasRenderingContext2D | null | undefined   // undefined: not asked yet; null: no canvas here
  return (s: string) => {
    const el = input()
    try {
      ctx ??= document.createElement('canvas').getContext('2d')
      if (ctx && el) {
        ctx.font = getComputedStyle(el).font
        ;(ctx as CanvasRenderingContext2D & { fontKerning?: string }).fontKerning = 'none'
        return ctx.measureText(s).width
      }
    } catch { /* no canvas (a test environment) */ }
    return s.length * 8
  }
}

export interface SearchFieldProps {
  value: string
  onChange: (v: string) => void
  placeholder: string
  suggestions: readonly Suggestion[]
  onPick: (s: Suggestion) => void
  /** The first time the box is used: the page loads the lists the suggestions come from. */
  onFirstUse?: () => void
  inputRef: RefObject<HTMLInputElement | null>
}

export function SearchField({ value, onChange, placeholder, suggestions, onPick, onFirstUse, inputRef }: SearchFieldProps) {
  const reduce = Boolean(useReducedMotion())
  const uid = useId()
  const box = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLDivElement>(null)
  const ghost = useRef<HTMLDivElement>(null)
  const phold = useRef<HTMLDivElement>(null)
  const glow = useRef<HTMLDivElement>(null)
  const stop = useRef<(() => void) | null>(null)
  const measureRef = useRef(makeMeasure(() => inputRef.current))
  const used = useRef(false)

  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [clearing, setClearing] = useState(false)
  const [fresh, setFresh] = useState(0)      // how many letters at the end were just typed (they spring in)
  const [caret, setCaret] = useState({ x: 0, show: false })
  const [scroll, setScroll] = useState(0)

  const shown = open && suggestions.length > 0
  const listId = `${uid}-list`
  const optId = (i: number) => `${uid}-opt-${i}`

  const syncCaret = useCallback(() => {
    const el = inputRef.current
    if (!el) return
    const at = el.selectionStart ?? el.value.length
    const end = el.selectionEnd ?? at
    setScroll(el.scrollLeft)
    setCaret({ x: measureRef.current(el.value.slice(0, at)) - el.scrollLeft, show: at === end && document.activeElement === el })
  }, [inputRef])
  const later = useCallback(() => { requestAnimationFrame(syncCaret) }, [syncCaret])
  useLayoutEffect(syncCaret, [value, syncCaret])
  useEffect(() => { setActive(-1) }, [value, suggestions.length])
  useEffect(() => () => stop.current?.(), [])
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const change = (next: string) => {
    const grew = next.length - value.length
    setFresh(grew > 0 ? grew : 0)   // the letters just added (counted from the end) spring in
    onChange(next)
    setOpen(true)
  }

  const clear = () => {
    const el = inputRef.current
    if (clearing || !value) return
    if (reduce || !ghost.current || !phold.current || !glow.current || !wrap.current || !field.current) { onChange(''); return }
    const keepFocus = document.activeElement === el
    const text = value
    setClearing(true)
    setFresh(0)
    onChange('')
    setOpen(false)
    stop.current = startClear({ ghost: ghost.current, placeholder: phold.current, glow: glow.current }, text, {
      measure: measureRef.current,
      padLeft: field.current.offsetLeft,
      width: wrap.current.clientWidth || 280,
      dark: document.documentElement.getAttribute('data-theme') !== 'light',
      onDone: () => {
        stop.current = null
        setClearing(false)
        if (keepFocus) requestAnimationFrame(() => el?.focus({ preventScroll: true }))
      },
    })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      if (shown) { setOpen(false); setActive(-1); return }
      if (value) { clear(); inputRef.current?.blur() }
      return
    }
    if (!suggestions.length) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      const n = suggestions.length
      setActive((a) => (e.key === 'ArrowDown' ? (a + 1) % n : (a - 1 + n) % n))
    } else if (e.key === 'Enter' && active >= 0 && suggestions[active]) {
      e.preventDefault()
      pick(suggestions[active]!)
    }
  }
  const pick = (s: Suggestion) => { setOpen(false); setActive(-1); onPick(s) }

  const chars = Array.from(value)
  const firstFresh = Math.max(0, chars.length - fresh)

  return (
    <div ref={box} className="relative min-w-0 max-w-[407px] flex-1">
      <div ref={wrap} className={cn('t-clear ax-search flex h-11 items-center gap-2.5 rounded-[14px] border border-line bg-surface pl-3.5 pr-2.5 text-faint focus-within:border-accent/50', value && 'has-value', clearing && 'is-clearing')}>
        <Icon name="search" className="shrink-0" />
        <div ref={field} className="ax-field relative h-full min-w-0 flex-1 text-base text-fg">
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => change(e.target.value)}
            onFocus={() => { if (!used.current) { used.current = true; onFirstUse?.() } setOpen(true); later() }}
            onBlur={later}
            onKeyDown={onKeyDown}
            onKeyUp={later}
            onSelect={later}
            onClick={later}
            onScroll={later}
            onInput={later}
            role="combobox"
            aria-label="Search"
            aria-autocomplete="list"
            aria-expanded={shown}
            aria-controls={listId}
            aria-activedescendant={active >= 0 ? optId(active) : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          <div className="ax-live" aria-hidden="true">
            <span className="ax-live-inner" style={{ transform: `translateX(${-scroll}px)` }}>
              {chars.map((c, i) => <span key={i} className={cn('ax-char', i >= firstFresh && 'ax-char-in')} style={{ '--i': Math.min(i - firstFresh, 12) } as React.CSSProperties}>{c}</span>)}
            </span>
          </div>
          <div ref={ghost} className="t-clear-mirror" aria-hidden="true" />
          <div ref={phold} className="t-clear-placeholder text-faint" aria-hidden="true">{placeholder}</div>
          <motion.span aria-hidden="true" className="ax-caret" data-show={caret.show && !clearing} animate={{ x: caret.x }} initial={false} transition={reduce ? { duration: 0 } : spring} />
        </div>
        {value ? (
          <button
            type="button"
            className="t-clear-btn grid size-7 shrink-0 place-items-center rounded-lg text-muted hover:bg-fg/5 hover:text-fg"
            aria-label="Clear search"
            onPointerDown={(e) => { if (document.activeElement === inputRef.current) e.preventDefault() }}
            onClick={clear}
          >
            <Icon name="close" className="size-4" />
          </button>
        ) : (
          <kbd className="hidden rounded-md border border-line-2 px-1.5 py-0.5 font-sans text-[11px] font-semibold sm:block">Ctrl K</kbd>
        )}
        <div ref={glow} className="t-clear-glow" aria-hidden="true" />
      </div>

      <AnimatePresence>
        {shown ? (
          <motion.div
            className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-xl border border-line-2 bg-surface shadow-[0_18px_50px_rgb(0_0_0/0.5)]"
            initial={reduce ? false : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0, transition: { duration: 0 } } : { opacity: 0, y: -4 }}
            transition={reduce ? { duration: 0 } : spring}
          >
            <div id={listId} role="listbox" aria-label="Matching performers and studios" className="max-h-80 overflow-y-auto py-1.5">
              <AnimatePresence mode="popLayout">
                {suggestions.map((s, i) => (
                  <motion.div
                    key={s.id}
                    id={optId(i)}
                    role="option"
                    aria-selected={i === active}
                    initial={reduce ? false : { opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={reduce ? { opacity: 0, transition: { duration: 0 } } : { opacity: 0 }}
                    transition={reduce ? { duration: 0 } : { ...spring, delay: i * 0.03 }}
                  >
                    <button type="button" tabIndex={-1} onClick={() => pick(s)} onMouseEnter={() => setActive(i)} className={cn('relative flex min-h-11 w-full items-center gap-3 px-3 text-left text-sm', reduce && i === active && 'bg-fg/5')}>
                      {i === active && !reduce ? <motion.span layoutId={`${uid}-hl`} transition={spring} className="absolute inset-x-1.5 inset-y-0.5 rounded-lg bg-fg/[0.07]" /> : null}
                      <span className="relative"><Tile s={s} /></span>
                      <span className="relative min-w-0 flex-1">
                        <span className="block truncate font-semibold"><Highlight text={s.title} query={value} /></span>
                        <span className="block truncate text-xs text-muted">{s.description}</span>
                      </span>
                    </button>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
