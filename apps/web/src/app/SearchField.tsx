/*
 * The header search box.
 *  - Typing: each new letter springs in, and a caret glides between letters (the idea of unlumen UI's "Animated Input"; written here from
 *    its public description, not from its paid source).
 *  - Clearing (×): the text dissolves (see design/clearDissolve.ts).
 *  - Suggestions: a keyboard-driven combobox that lists matching performers and studios with their picture, like a favicon search
 *    (after SmoothUI's favicon-search, MIT, Copyright (c) 2024 Eduardo Calvo).
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { ClearField, type ClearHandle } from '@/design/ClearField'
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
  const field = useRef<ClearHandle>(null)
  const used = useRef(false)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)

  const shown = open && suggestions.length > 0
  const listId = `${uid}-list`
  const optId = (i: number) => `${uid}-opt-${i}`

  useEffect(() => { setActive(-1) }, [value, suggestions.length])
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      if (shown) { setOpen(false); setActive(-1); return }
      if (value) { field.current?.clear(); inputRef.current?.blur() }
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

  return (
    <div ref={box} className="relative min-w-0 max-w-[407px] flex-1">
      <ClearField
        value={value}
        onChange={(v) => { onChange(v); setOpen(true) }}
        placeholder={placeholder}
        label="Search"
        clearLabel="Clear search"
        inputRef={inputRef}
        handle={field}
        onClear={() => setOpen(false)}
        className="h-11 rounded-[14px] border border-line bg-surface pl-3.5 pr-2.5"
        trailing={<kbd className="hidden rounded-md border border-line-2 px-1.5 py-0.5 font-sans text-[11px] font-semibold sm:block">Ctrl K</kbd>}
        inputProps={{
          role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': shown, 'aria-controls': listId,
          'aria-activedescendant': active >= 0 ? optId(active) : undefined,
          onFocus: () => { if (!used.current) { used.current = true; onFirstUse?.() } setOpen(true) },
          onKeyDown,
        }}
      />

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
