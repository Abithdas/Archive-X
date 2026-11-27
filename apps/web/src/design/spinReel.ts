import { prefersReduced } from './motionPref'
/*
 * A counter whose digits roll, written for Archive X with the Web Animations API. Each digit is a column that shows one cell of a strip of
 * the digits 0 to 9 twice; rolling slides the strip up to the new digit, the columns start one after another from the right, and the
 * slide ends with a small overshoot, so the neighbouring digit shows for a moment and then settles. The columns fade at their top and bottom
 * edges (motion.css, .mo-reel-col), so there is no need to draw the neighbours yourself.
 */
const CELL_EM = 1.2          // the height of one digit, in em (the same as in motion.css)
const COLUMN_GAP_MS = 70     // each column starts this much after the one on its left
const BASE_MS = 240
const PER_STEP_MS = 55       // a longer roll takes longer
const LAND = 'cubic-bezier(0.25, 1.3, 0.5, 1)'

interface Col { strip: HTMLElement; digit: number }

function build(el: HTMLElement, str: string): Col[] {
  el.replaceChildren()
  const cols: Col[] = []
  for (const ch of str) {
    if (ch < '0' || ch > '9') {
      const sep = document.createElement('span')
      sep.className = 'mo-reel-sep'
      sep.textContent = ch
      el.appendChild(sep)
      continue
    }
    const col = document.createElement('span')
    col.className = 'mo-reel-col'
    const sizer = document.createElement('span')
    sizer.className = 'mo-reel-sizer'
    sizer.textContent = '0'
    const strip = document.createElement('span')
    strip.className = 'mo-reel-strip'
    for (let i = 0; i < 20; i++) {
      const c = document.createElement('span')
      c.textContent = String(i % 10)
      strip.appendChild(c)
    }
    col.append(sizer, strip)
    el.appendChild(col)
    cols.push({ strip, digit: +ch })
  }
  return cols
}
// In em, like the cells themselves: a pixel count measured from the column is rounded to a whole pixel (15.6 px becomes 16), and every digit above 0 then sat a little off its cell, the higher digits the more.
const at = (digit: number) => `translateY(${(-digit * CELL_EM).toFixed(3)}em)`
const rest = (c: Col, digit: number) => { c.strip.style.transform = at(digit) }

/** Show `to` on the columns with no motion. */
export function showReel(el: HTMLElement, to: number): void {
  const str = to.toLocaleString('en-US')
  el.setAttribute('aria-label', str)
  build(el, str).forEach((c) => rest(c, c.digit))
}

/** Rolls `el` from one number to another and calls `onDone` when every column has settled. Returns a function that cancels it. */
export function rollReel(el: HTMLElement, from: number, to: number, onDone?: () => void): () => void {
  const str = to.toLocaleString('en-US')
  el.setAttribute('aria-label', str)
  const cols = build(el, str)
  if (prefersReduced() || !cols.length || typeof cols[0]!.strip.animate !== 'function') { cols.forEach((c) => rest(c, c.digit)); onDone?.(); return () => {} }
  const old = String(Math.max(0, Math.round(from))).padStart(cols.length, '0').slice(-cols.length)   // each column starts on its digit of the old number, counted from the right
  let waiting = 0
  let cancelled = false
  const anims: Animation[] = []
  cols.forEach((c, i) => {
    const start = +old[i]!
    const steps = (c.digit - start + 10) % 10
    rest(c, start)
    if (!steps) return
    waiting++
    const a = c.strip.animate(
      [{ transform: at(start) }, { transform: at(start + steps) }],
      { duration: BASE_MS + steps * PER_STEP_MS, delay: i * COLUMN_GAP_MS, easing: LAND, fill: 'both' },
    )
    anims.push(a)
    a.finished.then(() => {
      if (cancelled) return
      rest(c, c.digit)
      a.cancel()
      if (--waiting === 0) onDone?.()
    }, () => undefined)
  })
  if (!waiting) onDone?.()
  return () => { cancelled = true; anims.forEach((a) => a.cancel()) }
}
