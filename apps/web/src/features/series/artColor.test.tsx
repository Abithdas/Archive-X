import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TitleStage } from './TitleStage'
import { withRouter } from '@/test/withRouter'
import { accentStyle, dominantColour, hex, readableAccent, setArtColours, whiteContrast, COLOURFUL_SHARE, type Rgb } from './artColor'

/** RGBA pixel data of `n` pixels, each from the function. */
const pixels = (n: number, f: (i: number) => Rgb | [number, number, number]) => {
  const out = new Uint8ClampedArray(n * 4)
  for (let i = 0; i < n; i++) { const c = f(i); const [r, g, b] = Array.isArray(c) ? c : [c.r, c.g, c.b]; out.set([r!, g!, b!, 255], i * 4) }
  return out
}

describe('picking a colour from artwork', () => {
  it('takes the dominant colourful hue, not the dark or the grey around it', () => {
    const data = pixels(400, (i) => (i % 10 < 6 ? [10, 10, 12] : i % 10 < 8 ? [128, 128, 128] : i % 10 === 8 ? [220, 60, 40] : [200, 50, 40]))     // mostly black and grey, a little red
    const c = dominantColour(data)!
    expect(c.r).toBeGreaterThan(190); expect(c.g).toBeLessThan(80); expect(c.b).toBeLessThan(70)
  })
  it('lets the larger area win between two colours', () => {
    const data = pixels(300, (i) => (i < 200 ? [30, 90, 200] : [220, 40, 40]))
    const c = dominantColour(data)!
    expect(c.b).toBeGreaterThan(c.r)
  })
  it('finds nothing in a black-and-white picture, or an empty or see-through one', () => {
    expect(dominantColour(pixels(400, (i) => { const v = (i * 7) % 256; return [v, v, v] }))).toBeNull()
    expect(dominantColour(new Uint8ClampedArray(0))).toBeNull()
    expect(dominantColour(pixels(100, () => [200, 40, 40]).map((v, i) => (i % 4 === 3 ? 0 : v)))).toBeNull()              // fully transparent
    const rare = pixels(1000, (i) => (i < Math.floor(1000 * COLOURFUL_SHARE) - 2 ? [200, 40, 40] : [20, 20, 20]))
    expect(dominantColour(rare)).toBeNull()                                                                              // a speck of colour in a dark picture is not its colour
  })
  it('moves any colour to a shade white text reads on, and keeps its hue', () => {
    for (const c of [{ r: 255, g: 255, b: 0 }, { r: 250, g: 240, b: 60 }, { r: 0, g: 255, b: 255 }, { r: 255, g: 0, b: 0 }, { r: 30, g: 90, b: 200 }, { r: 120, g: 255, b: 40 }, { r: 255, g: 105, b: 180 }]) {
      const out = readableAccent(c)
      expect(whiteContrast(out)).toBeGreaterThanOrEqual(4.5)
      expect(whiteContrast(out)).toBeLessThan(15)                                                                         // and not black: it is still a colour
    }
    const yellow = readableAccent({ r: 255, g: 255, b: 0 })
    expect(yellow.r).toBeGreaterThan(yellow.b + 40)                                                                       // still yellowish
  })
  it('gives the accent and the text colour for a page', () => {
    const style = accentStyle({ r: 30, g: 90, b: 200 })
    expect(style['--color-accent']).toMatch(/^#[0-9a-f]{6}$/); expect(style['--color-accent-fg']).toBe('#ffffff')
    expect(hex({ r: 1, g: 2, b: 300 })).toBe('#0102ff')
  })
})

describe('the title page wears its artwork colour', () => {
  const RED = pixels(784, () => [220, 40, 40])
  let loads: string[]
  beforeEach(() => {
    loads = []
    setArtColours(true)
    vi.stubGlobal('Image', class { crossOrigin = ''; onload: (() => void) | null = null; onerror: (() => void) | null = null; set src(v: string) { loads.push(v); queueMicrotask(() => (v.includes('broken') ? this.onerror?.() : this.onload?.())) } })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(((kind: string) => (kind === '2d' ? { drawImage: () => undefined, getImageData: () => ({ data: RED }) } : null)) as never)
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); setArtColours(true) })
  const stage = (backdrop?: string) => withRouter(<TitleStage backdrop={backdrop} back="/" backLabel="Back" left={<p>left</p>} panelLabel="Panel" />)
  const root = () => screen.getByText('left').closest('div.relative') as HTMLElement

  it('sets the accent from the artwork, white for what sits on it, and asks for the picture once per address', async () => {
    stage('https://img.example/a.jpg')
    await screen.findByText('left')
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(root().style.getPropertyValue('--color-accent')).toMatch(/^#[0-9a-f]{6}$/)
    expect(root().style.getPropertyValue('--color-accent-fg')).toBe('#ffffff')
    expect(loads).toEqual(['https://img.example/a.jpg'])
  })
  it('leaves the accent alone for a picture that cannot be read, a title without artwork, and when it is switched off', async () => {
    const { unmount } = stage('https://img.example/broken.jpg')
    await screen.findByText('left')
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(root().style.getPropertyValue('--color-accent')).toBe('')
    unmount()
    const none = stage(undefined)
    await screen.findByText('left')
    expect(root().style.getPropertyValue('--color-accent')).toBe('')
    none.unmount()
    setArtColours(false)
    stage('https://img.example/b.jpg')
    await screen.findByText('left')
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(root().style.getPropertyValue('--color-accent')).toBe('')
    expect(loads).not.toContain('https://img.example/b.jpg')                              // switched off: the picture is not even read
  })
  it('is on unless turned off, and the choice is kept', () => {
    setArtColours(false)
    expect(localStorage.getItem('ax_next_art_colours')).toBe('off')
    setArtColours(true)
    expect(localStorage.getItem('ax_next_art_colours')).toBe('on')
    render(<p>x</p>)
  })
})
