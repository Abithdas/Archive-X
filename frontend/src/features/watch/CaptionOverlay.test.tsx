import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CaptionOverlay } from './CaptionOverlay'
import { DEFAULT_STYLE, SIZE_MAX, SIZE_MIN, clampPos, clampSize, loadStyle, normalizeStyle, saveStyle, sizeLabel } from './captionStyle'

beforeEach(() => localStorage.clear())

describe('caption style', () => {
  it('starts as outlined text with no box, near the bottom', () => {
    expect(DEFAULT_STYLE).toMatchObject({ bg: 'none', x: 0.5, size: 1 }); expect(DEFAULT_STYLE.y).toBeGreaterThan(0.8)
  })
  it('keeps size and position in range, and ignores junk', () => {
    expect(clampSize(9)).toBe(SIZE_MAX); expect(clampSize(0.1)).toBe(SIZE_MIN); expect(clampPos(2)).toBe(0.94); expect(clampPos(-1)).toBe(0.06)
    expect(normalizeStyle({ size: Number.NaN, bg: 'weird' as never, x: 5 })).toEqual({ ...DEFAULT_STYLE, x: 0.94 })
    expect(sizeLabel(1.35)).toBe('135%')
  })
  it('remembers what you chose', () => { saveStyle({ size: 1.5, bg: 'soft', x: 0.3, y: 0.2 }); expect(loadStyle()).toEqual({ size: 1.5, bg: 'soft', x: 0.3, y: 0.2 }) })
})

describe('CaptionOverlay', () => {
  const setup = (style = DEFAULT_STYLE) => {
    const moves: Array<[number, number]> = []; let ended = 0
    render(<div data-caption-frame><CaptionOverlay text="Hello there" style={style} onMove={(x, y) => moves.push([x, y])} onMoveEnd={() => { ended++ }} /></div>)
    const frame = document.querySelector('[data-caption-frame]') as HTMLElement
    frame.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500, x: 0, y: 0, toJSON() {} })
    return { moves, ended: () => ended, el: screen.getByTestId('caption') }
  }
  it('has no box by default, and a box only when chosen', () => {
    expect(setup().el.className).not.toMatch(/bg-black/)
    document.body.innerHTML = ''
    expect(setup({ ...DEFAULT_STYLE, bg: 'soft' }).el.className).toMatch(/bg-black\/45/)
  })
  it('is placed where the style says', () => {
    const { el } = setup({ ...DEFAULT_STYLE, x: 0.25, y: 0.5 }); expect(el.style.left).toBe('25%'); expect(el.style.top).toBe('50%')
  })
  it('moves when dragged, and saves when let go', () => {
    const { moves, ended, el } = setup()
    el.setPointerCapture = vi.fn()
    const ev = (type: string, x: number, y: number) => { const e = new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }); Object.defineProperty(e, 'pointerId', { value: 1 }); return e }
    el.dispatchEvent(ev('pointerdown', 500, 440))
    el.dispatchEvent(ev('pointermove', 300, 250))
    expect(moves.at(-1)).toEqual([0.3, 0.5])
    el.dispatchEvent(ev('pointerup', 300, 250))
    expect(ended()).toBe(1)
  })
  it('moves with the arrow keys', () => {
    const { moves, ended, el } = setup()
    fireEvent.keyDown(el, { key: 'ArrowUp', shiftKey: true })
    expect(moves.at(-1)![1]).toBeCloseTo(0.83, 2); expect(ended()).toBe(1)
  })
})
