import { render } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, it } from 'vitest'
import { useScrollRow } from './useScrollRow'

function Row() {
  const ref = useRef<HTMLUListElement>(null)
  useScrollRow(ref)
  return <ul data-testid="row" ref={ref}><li>a</li></ul>
}
/** jsdom has no layout, so the row's size is set by hand. */
const sized = (el: HTMLElement, scrollWidth: number, clientWidth: number) => {
  Object.defineProperty(el, 'scrollWidth', { value: scrollWidth, configurable: true })
  Object.defineProperty(el, 'clientWidth', { value: clientWidth, configurable: true })
}

describe('useScrollRow', () => {
  it('turns the mouse wheel into a sideways scroll while the row has room', () => {
    const { getByTestId } = render(<Row />)
    const el = getByTestId('row')
    sized(el, 600, 300)
    const e = new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true })
    el.dispatchEvent(e)
    expect(el.scrollLeft).toBe(120)
    expect(e.defaultPrevented).toBe(true)
  })
  it('leaves the wheel to the page at the end of the row and when nothing overflows', () => {
    const { getByTestId } = render(<Row />)
    const el = getByTestId('row')
    sized(el, 300, 300)
    const none = new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true })
    el.dispatchEvent(none)
    expect(none.defaultPrevented).toBe(false)
    sized(el, 600, 300)
    el.scrollLeft = 300
    const end = new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true })
    el.dispatchEvent(end)
    expect(end.defaultPrevented).toBe(false)
  })
  it('scrolls when dragged with the mouse', () => {
    const { getByTestId } = render(<Row />)
    const el = getByTestId('row')
    sized(el, 600, 300)
    const pointer = (type: string, target: EventTarget, clientX: number) => {   // jsdom has no PointerEvent: a mouse event that says it is a mouse
      const e = Object.assign(new MouseEvent(type, { clientX, button: 0, bubbles: true }), { pointerType: 'mouse' })
      target.dispatchEvent(e)
    }
    pointer('pointerdown', el, 200)
    pointer('pointermove', window, 150)
    expect(el.scrollLeft).toBe(50)
    pointer('pointerup', window, 150)
  })
})
