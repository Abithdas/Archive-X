import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from './cn'

/** How long the cross-fade takes, the same number as `--reveal-dur` in styles.css. */
const REVEAL_MS = 400
type Phase = 'wait' | 'swap' | 'done'

/**
 * Skeleton loader and reveal (transitions.dev). While `ready` is false the placeholder shows and pulses, in the flow, so it holds the slot's height.
 * When `ready` turns true the content is put in the flow, the placeholder lies over it, and the two cross-fade (opacity and a small blur);
 * then the placeholder is removed and the content is on its own, with no wrapper between it and its parent (`display: contents`).
 * Content that is there on the first render (cached data) is shown at once: nothing flashes.
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
    <div className={cn('t-skel', phase === 'swap' && 'is-layered', revealed && 'is-revealed', className)} data-state={phase === 'wait' ? 'loading' : phase === 'swap' ? 'revealing' : 'ready'}>
      {phase !== 'done' ? <div className={cn('t-skel-skeleton', phase === 'wait' && 'is-pulsing')} aria-hidden={phase === 'swap' ? true : undefined}>{skeleton}</div> : null}
      {phase !== 'wait' ? <div className={phase === 'done' ? 't-skel-done' : 't-skel-content'}>{children}</div> : null}
    </div>
  )
}
