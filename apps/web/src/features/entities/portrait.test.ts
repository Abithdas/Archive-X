import { beforeEach, describe, expect, it } from 'vitest'
import { DEFAULT_CROP, imageStyle, STORAGE_KEY, cropVars, dragCrop, isDefaultCrop, maxPan, normalizeCrop, readCrop, startDrag, withZoom, writeCrop } from './portrait'

beforeEach(() => localStorage.clear())

describe('portrait framing', () => {
  it('limits zoom to 1–2.5 and treats junk as the default', () => {
    expect(normalizeCrop({ zoom: 9 }).zoom).toBe(2.5)
    expect(normalizeCrop({ zoom: 0.2 }).zoom).toBe(1)
    expect(normalizeCrop({ zoom: Number.NaN, x: 'a' as never })).toEqual(DEFAULT_CROP)
    expect(normalizeCrop(null)).toEqual(DEFAULT_CROP)
  })
  it('keeps the photo covering its frame: pan is limited by zoom', () => {
    expect(maxPan(1)).toBe(0)
    expect(maxPan(2)).toBeCloseTo(47)
    expect(normalizeCrop({ zoom: 1, x: 30, y: -30 })).toEqual({ x: 0, y: 0, zoom: 1 })
    expect(normalizeCrop({ zoom: 2, x: 99, y: -99 })).toEqual({ x: 47, y: -47, zoom: 2 })
  })
  it('saves per performer under the classic key', () => {
    expect(writeCrop(7, { x: 10, y: -5, zoom: 1.5 })).toBe(true)
    expect(readCrop(7)).toEqual({ x: 10, y: -5, zoom: 1.5 })
    expect(readCrop(8)).toEqual(DEFAULT_CROP)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)['7'].zoom).toBe(1.5)
  })
  it('reads classic-saved data and survives corrupt storage', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ 3: { x: 4, y: 4, zoom: 1.4 } }))
    expect(readCrop(3)).toEqual({ x: 4, y: 4, zoom: 1.4 })
    localStorage.setItem(STORAGE_KEY, '[1,2')
    expect(readCrop(3)).toEqual(DEFAULT_CROP)
    localStorage.setItem(STORAGE_KEY, '[]')
    expect(readCrop(3)).toEqual(DEFAULT_CROP)
  })
  it('reports a refused write instead of throwing', () => {
    const real = Storage.prototype.setItem
    Storage.prototype.setItem = () => { throw new Error('quota') }
    try { expect(writeCrop(1, DEFAULT_CROP)).toBe(false) } finally { Storage.prototype.setItem = real }
  })
  it('the first drag on an unzoomed photo zooms to 115%, later drags keep the zoom', () => {
    expect(startDrag(DEFAULT_CROP).zoom).toBe(1.15)
    expect(startDrag({ x: 0, y: 0, zoom: 2 }).zoom).toBe(2)
  })
  it('dragging moves the photo by the pointer distance as a percentage of the frame, within the limit', () => {
    const from = { x: 0, y: 0, zoom: 2 }
    expect(dragCrop(from, 26, -52, 260, 520)).toEqual({ x: 10, y: -10, zoom: 2 })
    expect(dragCrop(from, 5000, 0, 260, 520).x).toBeCloseTo(47)
    expect(dragCrop(from, 10, 10, 0, 0)).toBe(from)
  })
  it('changing zoom pulls the pan back inside the new limit', () => {
    const w = withZoom({ x: 40, y: 0, zoom: 2 }, 1.2)
    expect(w.x).toBeCloseTo(9.4); expect(w.zoom).toBe(1.2)
  })
  it('exposes the crop as CSS variables and knows the default', () => {
    expect(cropVars({ x: 5, y: -2, zoom: 1.3 })).toEqual({ '--portrait-pan-x': '5%', '--portrait-pan-y': '-2%', '--portrait-zoom': '1.3' })
    expect(isDefaultCrop(DEFAULT_CROP)).toBe(true)
    expect(isDefaultCrop({ x: 0, y: 0, zoom: 1.1 })).toBe(false)
  })
  it('builds the image style from the crop', () => {
    expect(imageStyle({ x: 5, y: -2, zoom: 1.3 })).toEqual({ objectPosition: 'center 25%', transform: 'translate(5%, -2%) scale(1.3)', transformOrigin: 'center center' })
  })
})
