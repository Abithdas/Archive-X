import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LikeHeart, seedParticles } from './LikeHeart'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

const heart = (c: HTMLElement) => c.querySelector('.mo-like') as HTMLElement

describe('seedParticles', () => {
  it('gives every dot its own direction, speed, delay and size', () => {
    const dots = Array.from({ length: 8 }, () => document.createElement('i'))
    let n = 0
    seedParticles(dots, () => ((n++ * 0.37) % 1))
    const px = dots.map((d) => d.style.getPropertyValue('--a'))
    expect(new Set(px).size).toBe(8)
    for (const d of dots) {
      for (const v of ['--a', '--d', '--dur', '--delay', '--e', '--s']) expect(d.style.getPropertyValue(v)).not.toBe('')
      expect(parseFloat(d.style.getPropertyValue('--s'))).toBeGreaterThanOrEqual(2)
      expect(parseFloat(d.style.getPropertyValue('--e'))).toBeLessThanOrEqual(0.75)
    }
  })
})

describe('LikeHeart', () => {
  it('shows a heart that was liked all along as filled, without the pop or the burst', () => {
    const { container } = render(<LikeHeart liked />)
    const el = heart(container)
    expect(el.dataset.liked).toBe('true')
    expect(el.hasAttribute('data-still')).toBe(true)
    expect(el.classList.contains('is-bursting')).toBe(false)
  })
  it('fills with a pop and a burst when it is liked, and the burst clears itself', () => {
    const { container, rerender } = render(<LikeHeart liked={false} />)
    rerender(<LikeHeart liked />)
    const el = heart(container)
    expect(el.dataset.liked).toBe('true')
    expect(el.hasAttribute('data-still')).toBe(false)
    expect(el.classList.contains('is-bursting')).toBe(true)
    expect(container.querySelectorAll('.mo-sparks i')).toHaveLength(8)
    expect((container.querySelector('.mo-sparks i') as HTMLElement).style.getPropertyValue('--a')).not.toBe('')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(el.classList.contains('is-bursting')).toBe(false)
  })
  it('empties quietly when it is unliked, and bursts again the next time it is liked', () => {
    const { container, rerender } = render(<LikeHeart liked={false} />)
    rerender(<LikeHeart liked />)
    rerender(<LikeHeart liked={false} />)
    const el = heart(container)
    expect(el.dataset.liked).toBe('false')
    expect(el.classList.contains('is-bursting')).toBe(false)
    rerender(<LikeHeart liked />)
    expect(el.classList.contains('is-bursting')).toBe(true)
  })
  it('is a plain decoration for assistive technology', () => {
    const { container } = render(<LikeHeart liked />)
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelector('.mo-sparks')?.getAttribute('aria-hidden')).toBe('true')
  })
})
