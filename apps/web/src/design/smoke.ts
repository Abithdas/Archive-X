import { prefersReduced } from './motionPref'
/*
 * The delete effect: the card is displaced and blurred by animated noise while it lifts and fades, and soft smoke puffs rise from where it was.
 * Written for Archive X.
 * It plays on the real element, so call it after the delete worked and before the item leaves the list; the rows below then slide up.
 */
export const SMOKE_MS = 720
const SVG_NS = 'http://www.w3.org/2000/svg'

export interface Puff { x: number; y: number; size: number; dx: number; dy: number; delay: number; dur: number; opacity: number }

/** Where the smoke starts and where it drifts: spread across the card, mostly from the lower half, rising and spreading sideways. */
export function puffs(width: number, height: number, count = 12, random: () => number = Math.random): Puff[] {
  return Array.from({ length: count }, (_, i) => {
    const size = Math.max(36, Math.min(width, height) * (0.35 + random() * 0.4))
    return {
      x: Math.round(random() * width), y: Math.round(height * (0.35 + random() * 0.65)), size: Math.round(size),
      dx: Math.round((random() - 0.5) * width * 0.5), dy: -Math.round(height * (0.35 + random() * 0.55)),
      delay: Math.round(i * 22 + random() * 80), dur: Math.round(SMOKE_MS * (0.9 + random() * 0.5)), opacity: Number((0.35 + random() * 0.35).toFixed(2)),
    }
  })
}

const ease = (t: number) => 1 - (1 - t) ** 3
const smooth = (a: number, b: number, t: number) => { const x = Math.max(0, Math.min(1, (t - a) / (b - a))); return x * x * (3 - 2 * x) }
const reduced = () => prefersReduced()

/** The animated filter: turbulence pushes the pixels apart (displacement) and a blur grows; both are driven per frame. */
function makeFilter(id: string) {
  let defs = document.getElementById('ax-smoke-defs') as unknown as SVGSVGElement | null
  if (!defs) {
    defs = document.createElementNS(SVG_NS, 'svg') as SVGSVGElement
    defs.id = 'ax-smoke-defs'
    defs.setAttribute('width', '0'); defs.setAttribute('height', '0'); defs.setAttribute('aria-hidden', 'true')
    defs.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden'
    document.body.appendChild(defs)
  }
  const filter = document.createElementNS(SVG_NS, 'filter')
  filter.setAttribute('id', id); filter.setAttribute('x', '-25%'); filter.setAttribute('y', '-25%'); filter.setAttribute('width', '150%'); filter.setAttribute('height', '150%')
  filter.setAttribute('color-interpolation-filters', 'sRGB')
  const noise = document.createElementNS(SVG_NS, 'feTurbulence')
  noise.setAttribute('type', 'fractalNoise'); noise.setAttribute('baseFrequency', '0.014 0.03'); noise.setAttribute('numOctaves', '2'); noise.setAttribute('seed', String(1 + Math.floor(Math.random() * 90))); noise.setAttribute('result', 'n')
  const displace = document.createElementNS(SVG_NS, 'feDisplacementMap')
  displace.setAttribute('in', 'SourceGraphic'); displace.setAttribute('in2', 'n'); displace.setAttribute('scale', '0'); displace.setAttribute('xChannelSelector', 'R'); displace.setAttribute('yChannelSelector', 'G'); displace.setAttribute('result', 'd')
  const blur = document.createElementNS(SVG_NS, 'feGaussianBlur')
  blur.setAttribute('in', 'd'); blur.setAttribute('stdDeviation', '0')
  filter.append(noise, displace, blur)
  defs.appendChild(filter)
  return { displace, blur, remove: () => filter.remove() }
}

let counter = 0

/** Plays the effect on `el` and resolves when it is almost gone (the smoke is still drifting), leaving `el` invisible. Never throws. */
export function smokeOut(el: HTMLElement | null | undefined): Promise<void> {
  if (!el || !el.isConnected) return Promise.resolve()
  if (reduced() || typeof requestAnimationFrame !== 'function') { el.style.opacity = '0'; return Promise.resolve() }
  const rect = el.getBoundingClientRect()
  if (rect.width < 4 || rect.height < 4) { el.style.opacity = '0'; return Promise.resolve() }
  const id = `ax-smoke-${++counter}`
  let f: ReturnType<typeof makeFilter> | null = null
  try { f = makeFilter(id) } catch { el.style.opacity = '0'; return Promise.resolve() }
  const layer = document.createElement('div')
  layer.className = 'ax-smoke-layer'
  layer.style.cssText = `left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px`
  for (const p of puffs(rect.width, rect.height)) {
    const d = document.createElement('i')
    d.className = 'ax-puff'
    d.style.cssText = `--x:${p.x}px;--y:${p.y}px;--s:${p.size}px;--dx:${p.dx}px;--dy:${p.dy}px;--delay:${p.delay}ms;--dur:${p.dur}ms;--o:${p.opacity}`
    layer.appendChild(d)
  }
  document.body.appendChild(layer)
  const reach = Math.max(70, Math.min(220, rect.width * 0.4))
  el.style.willChange = 'filter, opacity, transform'
  el.style.filter = `url(#${id})`
  el.style.pointerEvents = 'none'
  const t0 = performance.now()
  return new Promise((resolve) => {
    const frame = (now: number) => {
      const t = Math.min(1, (now - t0) / SMOKE_MS)
      const e = ease(t)
      f!.displace.setAttribute('scale', (reach * e).toFixed(1))
      f!.blur.setAttribute('stdDeviation', (8 * e).toFixed(2))
      el.style.opacity = String(1 - smooth(0.25, 0.95, t))
      el.style.transform = `translateY(${(-16 * e).toFixed(1)}px) scale(${(1 + 0.03 * e).toFixed(3)})`
      if (t < 1) { requestAnimationFrame(frame); return }
      el.style.opacity = '0'
      resolve()
      setTimeout(() => { f!.remove(); layer.remove() }, 700)   // the smoke finishes drifting after the card is gone
    }
    requestAnimationFrame(frame)
  })
}

/** The same, for the element carrying `data-smoke="<key>"` (the card or row that the delete belongs to). */
const esc = (v: string) => (typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(v) : v.replace(/["\\]/g, '\\$&'))
export const smokeKey = (key: string): Promise<void> => smokeOut(document.querySelector<HTMLElement>(`[data-smoke="${esc(key)}"]`))
