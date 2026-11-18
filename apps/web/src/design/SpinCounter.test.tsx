import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SpinCounter } from './SpinCounter'
import { rollReel, showReel } from './spinReel'
import { setMotion } from './motionPref'

describe('SpinCounter', () => {
  it('builds one column per digit, with a strip of the digits twice, and keeps separators as text', () => {
    const { container, getByRole } = render(<SpinCounter value={1741} />)
    expect(getByRole('img', { name: '1,741' })).toBeInTheDocument()
    expect(container.querySelectorAll('.mo-reel-col')).toHaveLength(4)
    expect(container.querySelectorAll('.mo-reel-col')[0]!.querySelectorAll('.mo-reel-strip > span')).toHaveLength(20)
    expect(container.querySelector('.mo-reel-sep')?.textContent).toBe(',')
  })
  it('names the new value for assistive technology as soon as it changes', () => {
    const { getByRole, rerender } = render(<SpinCounter value={100} />)
    rerender(<SpinCounter value={389} />)
    expect(getByRole('img', { name: '389' })).toBeInTheDocument()
  })
})

describe('the strip behind the digits', () => {
  const column = (el: HTMLElement) => el.querySelector('.mo-reel-strip') as HTMLElement
  it('has the digits 0 to 9 twice in each column, and rests on the shown one', () => {
    const el = document.createElement('span')
    showReel(el, 7)
    const strip = column(el)
    expect(strip.children).toHaveLength(20)
    expect([...strip.children].slice(0, 10).map((c) => c.textContent).join('')).toBe('0123456789')
    expect(el.getAttribute('aria-label')).toBe('7')
    expect(parseFloat(strip.style.transform.replace('translateY(', ''))).toBeLessThan(0)   // moved up to the 7
  })
  it('puts a column for every digit and leaves the separators as text', () => {
    const el = document.createElement('span')
    showReel(el, 1234)
    expect(el.querySelectorAll('.mo-reel-col')).toHaveLength(4)
    expect(el.querySelector('.mo-reel-sep')?.textContent).toBe(',')
  })
  it('rolls only the columns that change, starting each one after the one before it, and reports when all have settled', async () => {
    setMotion('full')
    const calls: Array<{ opts: { delay: number; duration: number }; end: () => void }> = []
    const animate = function (this: HTMLElement, _f: unknown, opts: { delay: number; duration: number }) {
      let end!: () => void
      const finished = new Promise<void>((r) => { end = r })
      calls.push({ opts, end })
      return { finished, cancel: () => undefined } as unknown as Animation
    }
    ;(HTMLElement.prototype as unknown as { animate: unknown }).animate = animate
    try {
      const el = document.createElement('span')
      document.body.appendChild(el)
      const done = vi.fn()
      rollReel(el, 120, 175, done)               // 1 stays, 2 -> 7, 0 -> 5
      expect(calls).toHaveLength(2)
      expect(calls[1]!.opts.delay).toBeGreaterThan(calls[0]!.opts.delay)
      expect(done).not.toHaveBeenCalled()
      calls.forEach((c) => c.end())
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
      expect(done).toHaveBeenCalledTimes(1)
    } finally { delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate }
  })
})
