import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SpinCounter } from './SpinCounter'
import { bezierEase } from './spinReel'

describe('SpinCounter', () => {
  it('builds one drum of ten digits per digit and keeps separators as text', () => {
    const { container, getByRole } = render(<SpinCounter value={1741} />)
    expect(getByRole('img', { name: '1,741' })).toBeInTheDocument()
    expect(container.querySelectorAll('.t-reel-col')).toHaveLength(4)
    expect(container.querySelectorAll('.t-reel-col')[0]!.querySelectorAll('.t-reel-digit')).toHaveLength(10)
    expect(container.querySelector('.t-reel-sep')?.textContent).toBe(',')
  })
  it('names the new value for assistive technology as soon as it changes', () => {
    const { getByRole, rerender } = render(<SpinCounter value={100} />)
    rerender(<SpinCounter value={389} />)
    expect(getByRole('img', { name: '389' })).toBeInTheDocument()
  })
  it('only the target digit is visible on a drum at rest', () => {
    const { container } = render(<SpinCounter value={7} />)
    const shown = [...container.querySelectorAll<HTMLElement>('.t-reel-digit')].filter((c) => c.style.opacity === '1')
    expect(shown.map((c) => c.textContent)).toEqual(['7'])
  })
})

describe('bezierEase', () => {
  it('runs from 0 to 1 and follows the curve in between', () => {
    const e = bezierEase([0.35, -0.02, 0.43, 1])
    expect(e(0)).toBe(0); expect(e(1)).toBe(1)
    expect(e(0.5)).toBeGreaterThan(0.4); expect(e(0.5)).toBeLessThan(0.8)
  })
})
