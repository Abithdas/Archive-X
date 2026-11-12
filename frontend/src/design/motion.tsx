import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import type { ReactNode } from 'react'

export { AnimatePresence, motion }

/** Respect the OS "reduce motion" setting everywhere (motion components honour it through this config). */
export function MotionProvider({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>
}

/** Shared timings so every animation in the app feels like one system. */
export const timing = { fast: 0.12, base: 0.2, slow: 0.32 } as const
export const ease = [0.2, 0.75, 0.25, 1] as const

export const fadeUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: timing.base, ease } },
  exit: { opacity: 0, y: -4, transition: { duration: timing.fast } },
} as const

/** Card grid item: enters with a short stagger, leaves by collapsing, neighbours reflow via `layout`. */
export const cardMotion = (index: number) =>
  ({
    layout: true,
    initial: { opacity: 0, y: 10 },
    animate: { opacity: 1, y: 0, transition: { duration: timing.base, ease, delay: Math.min(index, 16) * 0.035 } },
    exit: { opacity: 0, scale: 0.96, transition: { duration: timing.fast } },
  }) as const

/** Page change: the new page rises in. Enter only, so the outgoing page never flashes the new content. */
export const fadeThrough = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: timing.base, ease } },
} as const

/** List row: slides in, slides out, and the rows around it glide into place. */
export const listItem = {
  layout: 'position',
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: timing.base, ease } },
  exit: { opacity: 0, x: -8, transition: { duration: timing.fast } },
} as const

/** The one spring for sliding highlights and pop-ups. */
export const spring = { type: 'spring', stiffness: 520, damping: 38, mass: 0.7 } as const
