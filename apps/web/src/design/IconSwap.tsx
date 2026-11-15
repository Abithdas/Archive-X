import type { ReactNode } from 'react'

/**
 * Two icons in one spot that trade places with a blur and a scale (the Transitions.dev "Icon swap"): `on` shows `onIcon`, otherwise `offIcon`.
 * Use it inside a button whose icon changes with its state (play/pause, mute/unmute, expand/collapse).
 */
export function IconSwap({ on, offIcon, onIcon, className }: { on: boolean; offIcon: ReactNode; onIcon: ReactNode; className?: string }) {
  return (
    <span className={`t-icon-swap ${className ?? ''}`} data-state={on ? 'b' : 'a'} aria-hidden="true">
      <span className="t-icon" data-icon="a">{offIcon}</span>
      <span className="t-icon" data-icon="b">{onIcon}</span>
    </span>
  )
}
