/*
 * The clear animation of a search or filter box, written for Archive X with the Web Animations API: the typed text sinks, blurs and fades out,
 * the placeholder drops in from above a moment later, and one soft light sweeps across the box. The styles of the three parts are in motion.css
 * (.mo-clear-*); the numbers are here.
 */

export interface ClearParts {
  /** Holds the text that was typed, only while it sinks away. */
  ghost: HTMLElement
  placeholder: HTMLElement
  glow: HTMLElement
}

const OUT_MS = 360
const IN_MS = 420
const IN_DELAY = 120
const SWEEP_MS = 720
const EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)'

/**
 * Play the clear. Returns a function that stops it early (leaving the elements tidy). `onDone` runs when the animation ends or is stopped.
 * The caller clears the real value; this only animates the leftovers. Where animations are not available it just tidies up at once.
 */
export function startClear(parts: ClearParts, text: string, o: { onDone: () => void }): () => void {
  const { ghost, placeholder, glow } = parts
  ghost.textContent = text.replace(/ /g, ' ')
  let finished = false
  let running: Animation[] = []
  const finish = () => {
    if (finished) return
    finished = true
    running.forEach((a) => a.cancel())
    ghost.textContent = ''
    o.onDone()
  }
  if (typeof ghost.animate !== 'function') { finish(); return finish }
  running = [
    ghost.animate([{ transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' }, { transform: 'translateY(14px)', opacity: 0, filter: 'blur(3px)' }], { duration: OUT_MS, easing: 'cubic-bezier(0.3, 0, 0.8, 0.6)', fill: 'forwards' }),
    placeholder.animate([{ transform: 'translateY(-12px)', opacity: 0, filter: 'blur(2px)' }, { transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' }], { duration: IN_MS, delay: IN_DELAY, easing: EASE, fill: 'both' }),
    glow.animate([{ opacity: 0, backgroundPosition: '-80% 0' }, { opacity: 1, offset: 0.3 }, { opacity: 0, backgroundPosition: '180% 0' }], { duration: SWEEP_MS, easing: 'ease-in-out' }),
  ]
  Promise.allSettled(running.map((a) => a.finished)).then(finish)
  return finish
}
