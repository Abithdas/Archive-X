/*
 * The "Input clear with dissolve" transition from Transitions.dev (Jakub Antalik; Transitions.dev License, see THIRD-PARTY-NOTICES.md),
 * written as plain functions: the typed text flies down, blurs and fades while a soft streak ignites under each word, and the placeholder
 * falls in from above. Timing and geometry come from the --clear-* and --glow-* CSS variables in transitions.css.
 */

/** A cubic-bezier(x1,y1,x2,y2) sampler, so the JavaScript easing matches the CSS one. */
export function bezier(str: string): (t: number) => number {
  const m = String(str).match(/cubic-bezier\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)/)
  if (!m) return (t) => t
  const [x1, y1, x2, y2] = m.slice(1).map(parseFloat) as [number, number, number, number]
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by
  return (t) => {
    if (t <= 0) return 0
    if (t >= 1) return 1
    let s = t
    for (let i = 0; i < 8; i++) {
      const dx = ((ax * s + bx) * s + cx) * s - t
      const d = (3 * ax * s + 2 * bx) * s + cx
      if (Math.abs(dx) < 1e-6 || d === 0) break
      s -= dx / d
    }
    return ((ay * s + by) * s + cy) * s
  }
}

/** One streak per word: a stack of soft ellipses along the bottom edge, centred under each word. `measure` gives the width of a piece of text. */
export function glowLayers(text: string, measure: (s: string) => number, o: { padLeft: number; width: number; spread: number; dark: boolean }): string {
  const rgb = o.dark ? '255,255,255' : '0,0,0'
  const layers: string[] = []
  let x = 0
  for (const seg of text.split(/(\s+)/)) {
    const w = measure(seg)
    if (seg.trim()) {
      const cx = o.padLeft + x + w / 2
      const hw = Math.max(w * 0.45, 8) * o.spread
      for (const [dx, rwm, rh, a] of [[0, 0.8, 7, 0.22], [hw * 0.45, 0.55, 8, 0.18], [-hw * 0.4, 0.65, 6, 0.16], [hw * 0.15, 0.9, 5, 0.14]] as const) {
        const lx = (((cx + dx) / o.width) * 100).toFixed(2)
        layers.push(`radial-gradient(ellipse ${Math.max(hw * rwm, 2).toFixed(1)}px ${rh}px at ${lx}% 100%, rgba(${rgb},${a}), transparent)`)
      }
    }
    x += w
  }
  return layers.join(', ')
}

const num = (root: HTMLElement, name: string, fallback: number) => {
  const v = parseFloat(getComputedStyle(root).getPropertyValue(name))
  return Number.isFinite(v) ? v : fallback
}

export interface ClearParts {
  /** Holds the text that was typed, only while it flies out. */
  ghost: HTMLElement
  placeholder: HTMLElement
  glow: HTMLElement
}

/**
 * Play the clear. Returns a function that stops it early (leaving the elements tidy). `onDone` runs when the animation ends or is stopped.
 * The caller clears the real value; this only animates the leftovers.
 */
export function startClear(parts: ClearParts, text: string, o: { measure: (s: string) => number; padLeft: number; width: number; dark: boolean; onDone: () => void }): () => void {
  const root = document.documentElement
  const total = num(root, '--clear-dur', 1000)
  const outDur = num(root, '--clear-out-dur', 400)
  const inDur = num(root, '--clear-in-dur', 400)
  const outFly = num(root, '--clear-out-fly', 12)
  const inFly = num(root, '--clear-in-fly', 12)
  const blur = num(root, '--clear-blur', 2)
  const delay = num(root, '--glow-delay', 50)
  const peakAt = num(root, '--glow-peak-at', 0.15)
  const gOp = num(root, '--glow-opacity', 0.42) * (o.dark ? 2 : 1)   // multiply vanishes on a dark surface; screen needs about twice the strength
  const spread = num(root, '--glow-spread', 1.5)
  const easeOut = bezier(getComputedStyle(root).getPropertyValue('--clear-out-ease'))
  const easeIn = bezier(getComputedStyle(root).getPropertyValue('--clear-in-ease'))
  const { ghost, placeholder: ph, glow } = parts

  ghost.textContent = text.replace(/ /g, ' ')
  glow.style.background = glowLayers(text, o.measure, { padLeft: o.padLeft, width: o.width, spread, dark: o.dark })
  glow.style.opacity = '0'
  ph.style.transform = `translateY(-${inFly}px)`
  ph.style.opacity = '0.9'
  ph.style.filter = `blur(${blur}px)`

  let raf = 0
  let stopped = false
  const finish = () => {
    ghost.style.cssText = ''
    ph.style.cssText = ''
    ghost.textContent = ''
    glow.style.opacity = '0'
    glow.style.background = ''
    o.onDone()
  }
  const t0 = performance.now()
  const tick = (now: number) => {
    if (stopped) return
    const el = now - t0
    const eo = easeOut(Math.min(1, el / outDur))
    ghost.style.transform = `translateY(${(eo * outFly).toFixed(1)}px)`
    ghost.style.opacity = (1 - eo).toFixed(3)
    ghost.style.filter = `blur(${(eo * blur).toFixed(1)}px)`
    const ei = easeIn(Math.min(1, el / inDur))
    ph.style.transform = `translateY(${(-inFly + ei * inFly).toFixed(1)}px)`
    ph.style.opacity = (0.9 + ei * 0.1).toFixed(3)
    ph.style.filter = `blur(${(blur - ei * blur).toFixed(1)}px)`
    let g = 0
    if (el > delay) {
      const gp = Math.min(1, (el - delay) / Math.max(1, total - delay))
      g = gp < peakAt ? gp / peakAt : 1 - (gp - peakAt) / (1 - peakAt)
    }
    glow.style.opacity = (g * gOp).toFixed(3)
    if (el < total) raf = requestAnimationFrame(tick)
    else finish()
  }
  raf = requestAnimationFrame(tick)
  return () => {
    if (stopped) return
    stopped = true
    cancelAnimationFrame(raf)
    finish()
  }
}
