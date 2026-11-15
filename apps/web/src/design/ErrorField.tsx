import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from './cn'

/** How long the shake runs (--shake-dur-a x 2 + --shake-dur-b x 2 in transitions.css), read at the moment it plays. */
export function shakeMs(root: HTMLElement = document.documentElement): number {
  const cs = getComputedStyle(root)
  const n = (name: string, fb: number) => { const v = parseFloat(cs.getPropertyValue(name)); return Number.isFinite(v) ? v : fb }
  return n('--shake-dur-a', 80) * 2 + n('--shake-dur-b', 60) * 2
}

/**
 * Wraps an input that has just been rejected. The input shakes left and right with overshoot (the Transitions.dev "Error state shake"),
 * gets a red ring, and its message fades in underneath. The error stays until `message` goes back to empty (the parent clears it when the
 * user types), so the text can still be read. `replay` restarts the shake when the same message comes again, e.g. a second wrong password.
 * With `quiet` there is no message line (the page already shows one elsewhere); only the shake and the ring.
 */
export function ErrorField({ message, replay = 0, quiet = false, className, wrapClassName, children }: { message: string; replay?: number; quiet?: boolean; className?: string; wrapClassName?: string; children: ReactNode }) {
  const [shaking, setShaking] = useState(false)
  const last = useRef(message)
  if (message) last.current = message    // keep the words on screen while the message fades out
  const on = Boolean(message)
  useEffect(() => {
    if (!on) { setShaking(false); return }
    setShaking(false)
    const frame = requestAnimationFrame(() => setShaking(true))   // off for a frame so the animation restarts from rest
    const done = setTimeout(() => setShaking(false), shakeMs() + 40)
    return () => { cancelAnimationFrame(frame); clearTimeout(done) }
  }, [on, message, replay])
  return (
    <div className={cn('t-input-wrap', on && 'is-error', wrapClassName)}>
      <div className={cn('t-input', on && 'is-error', shaking && 'is-shaking', className)}>{children}</div>
      {quiet ? null : <p role="alert" className="t-error-msg mt-1 text-sm">{last.current}</p>}
    </div>
  )
}
