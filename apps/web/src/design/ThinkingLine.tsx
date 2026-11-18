import { useEffect, useRef, useState } from 'react'
import { cn } from './cn'

interface Line { id: number; text: string; leaving: boolean; fresh: boolean }
const SWAP_MS = 150
const GAP_MS = 50

/**
 * One status line that shimmers while it holds and swaps to the next with a blurred up-and-down move whenever `text` changes
 * The text is real: whatever the assistant is doing right now.
 */
export function ThinkingLine({ text, className }: { text: string; className?: string }) {
  const next = useRef(1)
  const [lines, setLines] = useState<Line[]>([{ id: 0, text, leaving: false, fresh: false }])
  const current = lines.find((l) => !l.leaving)
  useEffect(() => {
    if (current?.text === text) return
    const id = next.current++
    setLines((ls) => [...ls.map((l) => ({ ...l, leaving: true })), { id, text, leaving: false, fresh: true }])
    const release = setTimeout(() => setLines((ls) => ls.map((l) => (l.id === id ? { ...l, fresh: false } : l))), GAP_MS)
    const clean = setTimeout(() => setLines((ls) => ls.filter((l) => !l.leaving)), SWAP_MS + GAP_MS + 50)
    return () => { clearTimeout(release); clearTimeout(clean) }
  }, [text]) // eslint-disable-line react-hooks/exhaustive-deps
  const widest = lines.reduce((a, l) => (l.text.length > a.length ? l.text : a), text)
  return (
    <span className={cn('mo-status', className)} role="status" aria-live="polite">
      <span className="mo-status-sizer" aria-hidden="true">{widest}</span>
      {lines.map((l) => <span key={l.id} className={cn('mo-status-text', l.leaving && 'is-exit', l.fresh && 'is-enter-start')} data-text={l.text} aria-hidden={l.leaving || undefined}>{l.text}</span>)}
    </span>
  )
}
