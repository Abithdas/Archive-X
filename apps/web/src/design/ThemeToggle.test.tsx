import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ThemeToggle, rayTarget } from './ThemeToggle'

describe('ThemeToggle', () => {
  it('says which theme it is and what pressing it does, and flips on click', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<ThemeToggle theme="dark" onToggle={onToggle} />)
    const b = screen.getByRole('button', { name: 'Dark theme. Activate to switch to light.' })
    expect(b).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(b)
    expect(onToggle).toHaveBeenCalledTimes(1)
    rerender(<ThemeToggle theme="light" onToggle={onToggle} />)
    expect(screen.getByRole('button', { name: 'Light theme. Activate to switch to dark.' })).toHaveAttribute('aria-pressed', 'false')
  })
  it('draws eight rays in the sun and the switch variant has a knob and stars', () => {
    const { container, rerender } = render(<ThemeToggle theme="light" onToggle={() => {}} />)
    expect(container.querySelectorAll('svg line')).toHaveLength(8)
    expect(container.querySelector('mask')).not.toBeNull()
    rerender(<ThemeToggle theme="dark" variant="switch" onToggle={() => {}} />)
    expect(container.querySelectorAll('span.rounded-full.bg-white').length).toBeGreaterThanOrEqual(6)   // the stars
  })
  it('keeps the rays out in the light theme and folds them in for the dark one', () => {
    expect(rayTarget('light')).toEqual({ opacity: 1, scale: 1 })
    expect(rayTarget('dark').opacity).toBe(0)
    expect(rayTarget('dark').scale).toBeLessThan(0.2)
  })
  it('is hidden from assistive technology except as one named button', () => {
    const { container } = render(<ThemeToggle theme="dark" onToggle={() => {}} />)
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })
})
