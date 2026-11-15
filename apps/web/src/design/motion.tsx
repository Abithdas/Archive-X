import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import type { ReactNode } from 'react'

export { AnimatePresence, motion }

/** Respect the OS "reduce motion" setting everywhere (motion components honour it through this config). */
export function MotionProvider({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>
}

/** Shared timings so every animation in the app feels like one system. */
const timing = { fast: 0.12, base: 0.2, slow: 0.32 } as const
const ease = [0.2, 0.75, 0.25, 1] as const


/**
 * How cards glide to a new place when others come in or go out (a scan adding videos shifts every card after them). A long, soft ease
 * out: a short or springy move reads as a jerk when a whole screen of cards moves at once.
 */
const glide = { duration: 0.6, ease: [0.22, 1, 0.36, 1] } as const

/**
 * Card grid item: a new card fades up in place; the others slide to their new spot (position only, so a card is never stretched on the way)
 * and a card that leaves just fades.
 */
export const cardMotion = (index: number) =>
  ({
    layout: 'position',
    initial: { opacity: 0, y: 12 },
    animate: { opacity: 1, y: 0, transition: { duration: timing.slow, ease, delay: Math.min(index, 16) * 0.035 } },
    exit: { opacity: 0, transition: { duration: timing.base } },
    transition: { layout: glide },
  }) as const

/** Page change: the new page rises in. Enter only, so the outgoing page never flashes the new content. */
export const fadeThrough = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: timing.base, ease } },
} as const

/** List row: slides in, slides out, and the rows around it glide into place. */
export const listItem = {
  layout: 'position',
  transition: { layout: glide },
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: timing.base, ease } },
  exit: { opacity: 0, x: -8, transition: { duration: timing.fast } },
} as const

/** The one spring for sliding highlights and pop-ups. */
export const spring = { type: 'spring', stiffness: 520, damping: 38, mass: 0.7 } as const

/**
 * A row in a review list, on the Transitions.dev "Panel reveal" curve: a new one slides up into place with a hint of blur, and when one is
 * removed the rows below it slide up to fill the gap on the same curve. (The removed one has already dissolved: see smoke.ts.)
 */
export const panelItem = {
  layout: 'position',
  initial: { opacity: 0, y: 24, filter: 'blur(2px)' },
  animate: { opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } },
  exit: { opacity: 0, transition: { duration: 0.12 } },
  transition: { layout: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } },
} as const
