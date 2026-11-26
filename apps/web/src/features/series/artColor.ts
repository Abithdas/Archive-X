import { useEffect, useState, useSyncExternalStore } from 'react'

/**
 * A title page takes an accent colour from its artwork, so a dark film and a bright cartoon do not wear the same colour. The colour is picked from the
 * artwork's own pixels (the dominant, saturated hue), then moved to a shade white text reads on. A black-and-white film has no colour to take and keeps
 * the person's accent. Done on this computer from the picture the page already shows; nothing is sent anywhere.
 */
export interface Rgb { r: number; g: number; b: number }

const luminance = ({ r, g, b }: Rgb) => {
  const f = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
/** WCAG contrast of white text on this colour. */
export const whiteContrast = (c: Rgb) => 1.05 / (luminance(c) + 0.05)

function toHsl({ r, g, b }: Rgb): { h: number; s: number; l: number } {
  const R = r / 255, G = g / 255, B = b / 255
  const max = Math.max(R, G, B), min = Math.min(R, G, B), d = max - min
  const l = (max + min) / 2
  if (d === 0) return { h: 0, s: 0, l }
  const s = d / (1 - Math.abs(2 * l - 1))
  const h = max === R ? ((G - B) / d + (G < B ? 6 : 0)) : max === G ? (B - R) / d + 2 : (R - G) / d + 4
  return { h: h * 60, s, l }
}
function fromHsl(h: number, s: number, l: number): Rgb {
  const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return { r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255) }
}
export const hex = ({ r, g, b }: Rgb) => '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join('')

/** Below this share of colourful pixels the picture counts as black and white (or nearly): there is no colour to take. */
export const COLOURFUL_SHARE = 0.06

/** The dominant colour of RGBA pixel data, or null for a picture with no colour of its own. Dark, bright and grey pixels do not vote; the rest vote by how colourful they are. */
export function dominantColour(data: ArrayLike<number>): Rgb | null {
  const buckets = Array.from({ length: 24 }, () => ({ w: 0, r: 0, g: 0, b: 0 }))
  let pixels = 0, colourful = 0
  for (let i = 0; i + 3 < data.length; i += 4) {
    if ((data[i + 3] ?? 0) < 200) continue
    pixels++
    const rgb = { r: data[i]!, g: data[i + 1]!, b: data[i + 2]! }
    const { h, s, l } = toHsl(rgb)
    if (l < 0.12 || l > 0.9 || s < 0.25) continue
    colourful++
    const w = s * (1 - Math.abs(2 * l - 1))
    const bucket = buckets[Math.floor(h / 15) % 24]!
    bucket.w += w; bucket.r += rgb.r * w; bucket.g += rgb.g * w; bucket.b += rgb.b * w
  }
  if (!pixels || colourful / pixels < COLOURFUL_SHARE) return null
  const best = buckets.reduce((a, b) => (b.w > a.w ? b : a))
  return best.w > 0 ? { r: Math.round(best.r / best.w), g: Math.round(best.g / best.w), b: Math.round(best.b / best.w) } : null
}

/** The colour moved to a shade that white text reads on (contrast of 4.5 or more) and that is still readable as text on a dark page; its colour is kept. */
export function readableAccent(rgb: Rgb): Rgb {
  const { h, s } = toHsl(rgb)
  let l = Math.min(0.55, Math.max(0.3, toHsl(rgb).l))
  let out = fromHsl(h, Math.min(0.85, Math.max(0.45, s)), l)
  for (let i = 0; i < 40 && whiteContrast(out) < 4.5; i++) { l -= 0.015; out = fromHsl(h, Math.min(0.85, Math.max(0.45, s)), l) }
  return out
}

/** The style that gives a page its title's colour: the accent, and white for what sits on it. */
export function accentStyle(rgb: Rgb): Record<string, string> {
  return { '--color-accent': hex(readableAccent(rgb)), '--color-accent-fg': '#ffffff' }
}

// ---- the switch (per computer, on unless turned off) ---------------------------------------------------------------------------------------------
const KEY = 'ax_next_art_colours'
const listeners = new Set<() => void>()
const read = () => { try { return localStorage.getItem(KEY) !== 'off' } catch { return true } }
let current = read()
export function setArtColours(on: boolean): void {
  current = on
  try { localStorage.setItem(KEY, on ? 'on' : 'off') } catch { /* private mode: it lasts until the page closes */ }
  listeners.forEach((l) => l())
}
export const useArtColours = (): boolean => useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb) } }, () => current, () => true)

// ---- reading the picture -------------------------------------------------------------------------------------------------------------------------
const SIZE = 28
const picked = new Map<string, Rgb | null>()

function loadPixels(url: string): Promise<Uint8ClampedArray | null> {
  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'                                  // a picture from a host that does not allow this simply cannot be read: the accent stays as it is
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = SIZE; canvas.height = SIZE
        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        if (!ctx) return resolve(null)
        ctx.drawImage(img, 0, 0, SIZE, SIZE)
        resolve(ctx.getImageData(0, 0, SIZE, SIZE).data)
      } catch { resolve(null) }
    }
    img.onerror = () => resolve(null)
    img.src = url
  })
}

/** The style for a title page from its artwork, or undefined (no artwork, a black-and-white one, one that cannot be read, or the switch is off). */
export function useArtworkAccent(url: string | undefined): Record<string, string> | undefined {
  const on = useArtColours()
  const [colour, setColour] = useState<Rgb | null>(() => (url ? picked.get(url) ?? null : null))
  useEffect(() => {
    if (!on || !url || !/^(https?:|\/|data:image\/)/.test(url)) { setColour(null); return }
    if (picked.has(url)) { setColour(picked.get(url) ?? null); return }
    let live = true
    void loadPixels(url).then((px) => {
      const found = px ? dominantColour(px) : null
      if (picked.size > 200) picked.clear()
      picked.set(url, found)
      if (live) setColour(found)
    })
    return () => { live = false }
  }, [on, url])
  return on && colour ? accentStyle(colour) : undefined
}
