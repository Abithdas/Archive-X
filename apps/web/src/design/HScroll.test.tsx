import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { HScroll, overflowState, wheelShift } from './HScroll'

describe('wheelShift', () => {
  it('turns an up-and-down wheel into sideways movement', () => expect(wheelShift(0, 100, 50, 400)).toBe(100))
  it('leaves sideways gestures, no overflow and the two ends to the browser', () => {
    expect(wheelShift(80, 10, 50, 400)).toBeNull()      // trackpad: already sideways
    expect(wheelShift(0, 100, 0, 0)).toBeNull()         // nothing to scroll
    expect(wheelShift(0, -100, 0, 400)).toBeNull()      // at the left end, wheel up scrolls the page
    expect(wheelShift(0, 100, 400, 400)).toBeNull()     // at the right end, wheel down scrolls the page
    expect(wheelShift(0, 0, 10, 400)).toBeNull()
  })
})

describe('overflowState', () => {
  it('needs the left arrow once scrolled, the right one while more is hidden', () => {
    expect(overflowState(0, 300, 900)).toEqual({ left: false, right: true })
    expect(overflowState(200, 300, 900)).toEqual({ left: true, right: true })
    expect(overflowState(600, 300, 900)).toEqual({ left: true, right: false })
    expect(overflowState(0, 300, 300)).toEqual({ left: false, right: false })
  })
})

class FakePointerEvent extends MouseEvent {
  pointerType: string
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerType?: string; pointerId?: number } = {}) {
    super(type, { bubbles: true, cancelable: true, ...init })
    this.pointerType = init.pointerType ?? 'mouse'
    this.pointerId = init.pointerId ?? 1
  }
}

beforeAll(() => {
  vi.stubGlobal('PointerEvent', FakePointerEvent)
  HTMLElement.prototype.setPointerCapture = () => {}
  HTMLElement.prototype.releasePointerCapture = () => {}
  HTMLElement.prototype.hasPointerCapture = () => true
})

function mount(scrollWidth: number) {
  const onPick = vi.fn()
  render(<HScroll label="Row"><button type="button" onClick={onPick}>Ava</button><button type="button">Mia</button></HScroll>)
  const box = screen.getByRole('group', { name: 'Row' })
  Object.defineProperty(box, 'clientWidth', { configurable: true, value: 300 })
  Object.defineProperty(box, 'scrollWidth', { configurable: true, value: scrollWidth })
  box.scrollBy = vi.fn() as unknown as typeof box.scrollBy
  act(() => { box.dispatchEvent(new Event('scroll')) })
  return { box, onPick }
}
const pointer = (el: Element, type: string, x: number, pointerType = 'mouse') => act(() => { el.dispatchEvent(new FakePointerEvent(type, { clientX: x, button: 0, pointerType })) })

describe('HScroll', () => {
  it('moves sideways with the mouse wheel and keeps the page still while it does', () => {
    const { box } = mount(900)
    const ev = new WheelEvent('wheel', { deltaY: 120, bubbles: true, cancelable: true })
    act(() => { box.dispatchEvent(ev) })
    expect(box.scrollLeft).toBe(120)
    expect(ev.defaultPrevented).toBe(true)
  })
  it('does not take the wheel when the row fits', () => {
    const { box } = mount(300)
    const ev = new WheelEvent('wheel', { deltaY: 120, bubbles: true, cancelable: true })
    act(() => { box.dispatchEvent(ev) })
    expect(ev.defaultPrevented).toBe(false)
  })
  it('shows an arrow only for the side that has more, and the arrows scroll', () => {
    const { box } = mount(900)
    expect(screen.queryByRole('button', { name: 'Scroll left' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Scroll right' }))
    expect(box.scrollBy).toHaveBeenCalledWith(expect.objectContaining({ left: 240 }))
    box.scrollLeft = 200
    act(() => { box.dispatchEvent(new Event('scroll')) })
    expect(screen.getByRole('button', { name: 'Scroll left' })).toBeInTheDocument()
  })
  it('has no arrows when everything fits', () => {
    mount(300)
    expect(screen.queryByRole('button', { name: /Scroll/ })).toBeNull()
  })
  it('drags with the mouse, and the end of a drag is not a click on what is underneath', () => {
    const { box, onPick } = mount(900)
    const ava = screen.getByRole('button', { name: 'Ava' })
    pointer(ava, 'pointerdown', 200)
    pointer(ava, 'pointermove', 120)
    expect(box.scrollLeft).toBe(80)
    pointer(ava, 'pointerup', 120)
    fireEvent.click(ava)
    expect(onPick).not.toHaveBeenCalled()
    fireEvent.click(ava)   // the next real click works
    expect(onPick).toHaveBeenCalledTimes(1)
  })
  it('a plain click, without moving, still picks', () => {
    const { onPick } = mount(900)
    const ava = screen.getByRole('button', { name: 'Ava' })
    pointer(ava, 'pointerdown', 200)
    pointer(ava, 'pointerup', 202)
    fireEvent.click(ava)
    expect(onPick).toHaveBeenCalledTimes(1)
  })
  it('leaves touch to the browser: a touch pointer does not start a drag', () => {
    const { box } = mount(900)
    const ava = screen.getByRole('button', { name: 'Ava' })
    pointer(ava, 'pointerdown', 200, 'touch')
    pointer(ava, 'pointermove', 100, 'touch')
    expect(box.scrollLeft).toBe(0)
  })
})
