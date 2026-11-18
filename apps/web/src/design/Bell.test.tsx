import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Bell } from './Bell'

const badge = (c: HTMLElement) => c.querySelector('.t-badge') as HTMLElement

describe('Bell', () => {
  it('has its badge closed with nothing to show and open with a count', () => {
    const { container, rerender } = render(<Bell count={0} />)
    expect(badge(container).dataset.open).toBe('false')
    rerender(<Bell count={3} />)
    expect(badge(container).dataset.open).toBe('true')
    expect(container.querySelector('.t-badge-dot')?.textContent).toBe('3')
  })
  it('keeps the number while the badge closes, so it does not go blank mid-animation', () => {
    const { container, rerender } = render(<Bell count={7} />)
    rerender(<Bell count={0} />)
    expect(badge(container).dataset.open).toBe('false')
    expect(container.querySelector('.t-badge-dot')?.textContent).toBe('7')
  })
  it('caps a big count and leaves the badge out of the accessibility tree (the button carries the count)', () => {
    const { container } = render(<Bell count={250} />)
    expect(container.querySelector('.t-badge-dot')?.textContent).toBe('99+')
    expect(badge(container).getAttribute('aria-hidden')).toBe('true')
  })
  it('is drawn in two parts, the bell and the clapper inside it, so each can swing on its own', () => {
    const { container } = render(<Bell count={0} />)
    expect(container.querySelector('.ax-bell-body')).not.toBeNull()
    expect(container.querySelector('.ax-bell-body .ax-bell-clapper')).not.toBeNull()
  })
  describe('ringing', () => {
    beforeEach(() => vi.useFakeTimers())
    afterEach(() => vi.useRealTimers())
    it('rings when a new notice arrives, then stops; it does not ring for a count that goes down or stays', () => {
      const { container, rerender } = render(<Bell count={0} />)
      const root = container.firstElementChild as HTMLElement
      expect(root).not.toHaveClass('is-ringing')
      rerender(<Bell count={1} />)
      expect(root).toHaveClass('is-ringing')
      act(() => { vi.advanceTimersByTime(1200) })
      expect(root).not.toHaveClass('is-ringing')
      rerender(<Bell count={1} />)
      rerender(<Bell count={0} />)
      expect(root).not.toHaveClass('is-ringing')
    })
  })
})
