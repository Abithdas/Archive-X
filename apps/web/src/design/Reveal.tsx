import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from './cn'

/** How long the cross-fade takes, the same number as the transitions of .mo-reveal in motion.css. */
const REVEAL_MS = 400
type Phase = 'wait' | 'swap' | 'done'

/**
 * A placeholder that gives way to real content. While `ready` is false the placeholder shows and pulses, in the flow, so it holds the slot's height.
 * When `ready` turns true the content is mounted in the same grid cell as the placeholder, so the slot is as tall as the taller of the two and
 * nothing collapses or jumps; the two cross-fade (opacity and a little blur) and then the placeholder is removed and the content is on its own,
 * with no wrapper between it and its parent (`display: contents`). Content that is there on the first render (cached data) is shown at once:
 * nothing flashes. The styles are in motion.css (.mo-reveal).
 */
export function Reveal({ ready, skeleton, children, className }: { ready: boolean; skeleton: ReactNode; children: ReactNode; className?: string }) {
  const [phase, setPhase] = useState<Phase>(ready ? 'done' : 'wait')
  const [revealed, setRevealed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const frame = useRef(0)
  useEffect(() => {
    if (!ready || phase !== 'wait') return
    setPhase('swap')
    frame.current = requestAnimationFrame(() => { frame.current = requestAnimationFrame(() => setRevealed(true)) })   // two frames, so the start state is painted before the change
    timer.current = setTimeout(() => setPhase('done'), REVEAL_MS + 60)
  }, [ready, phase])
  useEffect(() => () => { clearTimeout(timer.current); cancelAnimationFrame(frame.current) }, [])
  // Back to loading (a different query, an empty answer again): the placeholder returns without animating backwards.
  useEffect(() => { if (!ready && phase === 'done') { setPhase('wait'); setRevealed(false) } }, [ready, phase])
  return (
    <div className={cn('mo-reveal', revealed && 'is-revealed', className)} data-state={phase === 'wait' ? 'loading' : phase === 'swap' ? 'revealing' : 'ready'}>
      {phase !== 'done' ? <div className={cn('mo-reveal-skeleton', phase === 'wait' && 'is-pulsing')} aria-hidden={phase === 'swap' ? true : undefined}>{skeleton}</div> : null}
      {phase !== 'wait' ? <div className={phase === 'done' ? 'mo-reveal-done' : 'mo-reveal-content'}>{children}</div> : null}
    </div>
  )
}
