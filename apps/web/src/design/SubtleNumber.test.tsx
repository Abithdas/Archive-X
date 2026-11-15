import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SubtleNumber } from './SubtleNumber'
import { diffDigits } from './digitDiff'

describe('diffDigits', () => {
  it('marks only the characters that changed and keeps the old one to fade out', () => {
    expect(diffDigits('1,289', '1,290')).toEqual([{ ch: '1', changed: false }, { ch: ',', changed: false }, { ch: '2', changed: false }, { ch: '9', changed: true, old: '8' }, { ch: '0', changed: true, old: '9' }])
  })
  it('treats every character as new when the length changes', () => {
    expect(diffDigits('999', '1,000').every((p) => p.changed && p.old === undefined)).toBe(true)
  })
})

describe('SubtleNumber', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())
  it('shows plain text at first and for an unchanged value', () => {
    const { container, rerender } = render(<SubtleNumber value={1284} />)
    expect(container.textContent).toBe('1,284')
    rerender(<SubtleNumber value={1284} />)
    expect(container.querySelector('.is-animating')).toBeNull()
  })
  it('animates the changed digit upward when the value rises, then settles back to plain text', () => {
    const { container, rerender } = render(<SubtleNumber value={1284} />)
    rerender(<SubtleNumber value={1285} />)
    const num = container.querySelector('.t-subtle-number') as HTMLElement
    expect(num.classList.contains('is-animating')).toBe(true)
    expect(num.style.getPropertyValue('--subtle-dir')).toBe('1')
    expect(container.querySelectorAll('.t-subtle-old')).toHaveLength(1)
    expect(container.querySelector('.t-subtle-old')?.textContent).toBe('4')
    act(() => { vi.advanceTimersByTime(600) })
    expect(container.querySelector('.is-animating')).toBeNull()
    expect(container.textContent).toBe('1,285')
  })
  it('falls the other way when the value drops', () => {
    const { container, rerender } = render(<SubtleNumber value={50} />)
    rerender(<SubtleNumber value={40} />)
    expect((container.querySelector('.t-subtle-number') as HTMLElement).style.getPropertyValue('--subtle-dir')).toBe('-1')
  })
})
