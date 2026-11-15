import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MatrixLoader, dotDelays } from './MatrixLoader'
import { Shimmer } from './Shimmer'
import { StatusMark } from './StatusMark'
import { StreamText } from './StreamText'
import { ThinkingLine } from './ThinkingLine'
import { gapFor, isSpace, tokens } from './streamWords'

describe('stream words', () => {
  it('keeps the spacing exactly and tells words from gaps', () => {
    expect(tokens('Hello  big\nworld')).toEqual(['Hello', '  ', 'big', '\n', 'world'])
    expect(tokens('').length).toBe(0)
    expect(isSpace(' \n')).toBe(true); expect(isSpace('a')).toBe(false)
  })
  it('speeds up when many words are waiting, so the text never lags far behind', () => {
    expect(gapFor(1)).toBe(60); expect(gapFor(8)).toBe(60)
    expect(gapFor(40)).toBeLessThan(20); expect(gapFor(10000)).toBe(12)
  })
})

describe('StreamText', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())
  it('resolves words one by one, and only the new ones when more text arrives', () => {
    const { container, rerender } = render(<StreamText text="one two three" />)
    const lit = () => container.querySelectorAll('.t-stream-w.is-in').length
    expect(container.querySelectorAll('.t-stream-w')).toHaveLength(3); expect(lit()).toBe(0)
    const tick = () => act(() => { vi.advanceTimersByTime(61) })   // one word a gap
    tick(); expect(lit()).toBe(1); tick(); expect(lit()).toBe(2); tick(); expect(lit()).toBe(3)
    rerender(<StreamText text="one two three four" />)
    expect(container.querySelectorAll('.t-stream-w')).toHaveLength(4); expect(lit()).toBe(3)   // the first three stay put
    tick(); expect(lit()).toBe(4)
    expect(container.textContent).toBe('one two three four')
  })
  it('shows an older message at once', () => {
    const { container } = render(<StreamText text="already here" instant />)
    expect(container.querySelectorAll('.t-stream-w.is-in')).toHaveLength(2)
  })
})

describe('ThinkingLine', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())
  it('swaps to the new line: the old one leaves while the new one comes in, then the old one is gone', () => {
    const { container, rerender } = render(<ThinkingLine text="Thinking…" />)
    rerender(<ThinkingLine text="Checking diagnostics" />)
    const texts = () => [...container.querySelectorAll('.t-think-text')]
    expect(texts()).toHaveLength(2)
    expect(texts()[0]!.classList.contains('is-exit')).toBe(true); expect(texts()[1]!.classList.contains('is-enter-start')).toBe(true)
    act(() => { vi.advanceTimersByTime(60) })
    expect(texts()[1]!.classList.contains('is-enter-start')).toBe(false)
    act(() => { vi.advanceTimersByTime(300) })
    expect(texts()).toHaveLength(1); expect(texts()[0]!.textContent).toBe('Checking diagnostics')
    expect(texts()[0]!.getAttribute('data-text')).toBe('Checking diagnostics')   // the shimmer layer copies the same words
  })
  it('does nothing when the text has not changed', () => {
    const { container, rerender } = render(<ThinkingLine text="Same" />)
    rerender(<ThinkingLine text="Same" />)
    expect(container.querySelectorAll('.t-think-text')).toHaveLength(1)
  })
})

describe('matrix loader', () => {
  it('has a delay table per pattern, with holes at the corners when rounded', () => {
    expect(dotDelays('scan').slice(0, 5)).toEqual([0, 120, 240, 360, 0])
    expect(dotDelays('orbit')[5]).toBeNull()                       // the centre holds still
    expect(dotDelays('pulse').filter((d) => d === 0)).toHaveLength(4)
    expect(dotDelays('twinkle', true).filter((d) => d === 'gap')).toHaveLength(4)
    expect(new Set(dotDelays('twinkle')).size).toBe(16)
  })
  it('draws sixteen dots', () => {
    const { container, getByRole } = render(<MatrixLoader variant="orbit" label="Setting up" />)
    expect(container.querySelectorAll('i')).toHaveLength(16)
    expect(getByRole('status', { name: 'Setting up' })).toBeInTheDocument()
  })
})

describe('status mark and shimmer', () => {
  it('is one drawing whose state changes, named for assistive technology', () => {
    const { getByRole, rerender } = render(<StatusMark state="active" />)
    expect(getByRole('img', { name: 'Working' })).toHaveAttribute('data-state', 'active')
    rerender(<StatusMark state="done" />)
    expect(getByRole('img', { name: 'Done' })).toHaveAttribute('data-state', 'done')
    rerender(<StatusMark state="error" />)
    expect(getByRole('img', { name: 'Failed' })).toBeInTheDocument()
  })
  it('copies its words into data-text for the highlight layer', () => {
    const { container } = render(<Shimmer text="Starting…" />)
    expect(container.firstElementChild).toHaveAttribute('data-text', 'Starting…')
    expect(container.textContent).toBe('Starting…')
  })
})
