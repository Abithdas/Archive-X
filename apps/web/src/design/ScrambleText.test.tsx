import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScrambleText } from './ScrambleText'
import { scrambleFrame } from './scramble'

describe('scrambleFrame', () => {
  const rand = () => 0
  it('keeps the settled letters, spaces and punctuation, and scrambles the rest', () => {
    expect(scrambleFrame('Studios & Networks', 0, rand)).toBe('AAAAAAA & AAAAAAAA')
    expect(scrambleFrame('Cast', 1, rand)).toBe('Cast')
    expect(scrambleFrame('Cast', 0.5, rand)).toBe('CaAA')
    expect(scrambleFrame('', 0.3, rand)).toBe('')
  })
})

describe('ScrambleText', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('shows the first value at once and decodes into a changed one', () => {
    const { rerender } = render(<ScrambleText text="Performers" />)
    expect(screen.getByLabelText('Performers')).toHaveTextContent('Performers')
    rerender(<ScrambleText text="Cast" />)
    act(() => { vi.advanceTimersByTime(200) })
    const mid = screen.getByLabelText('Cast').textContent ?? ''
    expect(mid).toHaveLength(4)                       // still shuffling: same length as the new word
    act(() => { vi.advanceTimersByTime(900) })
    expect(screen.getByLabelText('Cast')).toHaveTextContent('Cast')
  })

  it('does not animate when the person asked for less motion', () => {
    localStorage.setItem('ax_motion', 'system')
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    const { rerender } = render(<ScrambleText text="Tags" />)
    rerender(<ScrambleText text="Genres" />)
    expect(screen.getByLabelText('Genres')).toHaveTextContent('Genres')
    vi.unstubAllGlobals(); localStorage.removeItem('ax_motion')
  })
})
