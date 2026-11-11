import { describe, expect, it } from 'vitest'
import { IMAGE_WIDTHS, responsive, srcSet, widthFor, withWidth } from './responsive'

describe('responsive artwork', () => {
  it('adds the width to a URL with or without a query', () => { expect(withWidth('/api/performers/1/image', 480)).toBe('/api/performers/1/image?w=480'); expect(withWidth('/api/x/image?v=a', 240)).toBe('/api/x/image?v=a&w=240') })
  it('lists candidates, with the master as the largest when its width is known', () => {
    expect(srcSet('/i', [240, 480])).toBe('/i?w=240 240w, /i?w=480 480w')
    expect(srcSet('/i?r=f3', [320, 480, 640], 640)).toBe('/i?r=f3&w=320 320w, /i?r=f3&w=480 480w, /i?r=f3 640w')
  })
  it('picks the smallest allowed width that covers the display at the pixel ratio', () => {
    expect(widthFor(60, 2)).toBe(160); expect(widthFor(96, 2)).toBe(240); expect(widthFor(176, 2)).toBe(480); expect(widthFor(280, 2)).toBe(640); expect(widthFor(1000, 2)).toBe(960); expect(widthFor(100, 1)).toBe(160)
    expect(IMAGE_WIDTHS).toEqual([160, 240, 320, 480, 640, 960])
  })
  it('builds img props', () => {
    const p = responsive('/i', '50vw'); expect(p.sizes).toBe('50vw'); expect(p.srcSet).toContain('/i?w=960 960w'); expect(p.src).toBe('/i?w=480')
    expect(responsive('/i', '10vw', [240]).src).toBe('/i?w=240'); expect(responsive('/i?r=f3', '10vw', [320, 640], 640).src).toBe('/i?r=f3')
  })
})
