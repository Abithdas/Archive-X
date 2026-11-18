import type { ReactNode } from 'react'

/**
 * Two icons in one spot that trade places with a blur and a scale `on` shows `onIcon`, otherwise `offIcon`.
 * Use it inside a button whose icon changes with its state (play/pause, mute/unmute, expand/collapse).
 */
export function IconSwap({ on, offIcon, onIcon, className }: { on: boolean; offIcon: ReactNode; onIcon: ReactNode; className?: string }) {
  return (
    <span className={`mo-swap ${className ?? ''}`} data-state={on ? 'b' : 'a'} aria-hidden="true">
      <span className="mo-glyph" data-icon="a">{offIcon}</span>
      <span className="mo-glyph" data-icon="b">{onIcon}</span>
    </span>
  )
}
