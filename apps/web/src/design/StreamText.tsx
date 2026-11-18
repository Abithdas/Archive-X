import { prefersReduced } from './motionPref'
import { useEffect, useMemo, useState } from 'react'
import { cn } from './cn'
import { gapFor, isSpace, tokens } from './streamWords'

/**
 * Text that resolves word by word through a soft blur as it arrives (Transitions.dev "Streaming text"). Give it the text so far; words that
 * are already there stay put and each new one fades in. With `instant` (older messages, reduced motion) everything is simply shown.
 */
export function StreamText({ text, instant = false, className }: { text: string; instant?: boolean; className?: string }) {
  const parts = useMemo(() => tokens(text), [text])
  const total = useMemo(() => parts.filter((p) => !isSpace(p)).length, [parts])
  const [shown, setShown] = useState(instant ? Infinity : 0)
  const reduce = prefersReduced()
  useEffect(() => {
    if (instant || reduce) { setShown(Infinity); return }
    if (shown >= total) return
    const t = setTimeout(() => setShown((n) => n + 1), gapFor(total - shown))
    return () => clearTimeout(t)
  }, [shown, total, instant, reduce])
  let w = 0
  return (
    <span className={cn('whitespace-pre-wrap [overflow-wrap:anywhere]', className)}>
      {parts.map((p, i) => isSpace(p) ? p : <span key={i} className={cn('t-stream-w', w++ < shown && 'is-in')}>{p}</span>)}
    </span>
  )
}
