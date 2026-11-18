import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Reveal } from './Reveal'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())
const wrap = (el: HTMLElement) => el.closest('.t-skel') as HTMLElement

describe('Reveal', () => {
  it('shows the placeholder, pulsing and in the flow, while the content is not there', () => {
    render(<Reveal ready={false} skeleton={<i data-testid="sk" />}><b data-testid="real" /></Reveal>)
    expect(wrap(screen.getByTestId('sk')).dataset.state).toBe('loading')
    expect(screen.getByTestId('sk').parentElement).toHaveClass('t-skel-skeleton', 'is-pulsing')
    expect(screen.queryByTestId('real')).toBeNull()
  })
  it('shows content that is already there at once, with no placeholder and no animation', () => {
    render(<Reveal ready skeleton={<i data-testid="sk" />}><b data-testid="real" /></Reveal>)
    expect(screen.queryByTestId('sk')).toBeNull()
    expect(screen.getByTestId('real').parentElement).toHaveClass('t-skel-done')
  })
  it('cross-fades when the content arrives, then drops the placeholder', () => {
    const { rerender } = render(<Reveal ready={false} skeleton={<i data-testid="sk" />}><b data-testid="real" /></Reveal>)
    rerender(<Reveal ready skeleton={<i data-testid="sk" />}><b data-testid="real" /></Reveal>)
    const box = wrap(screen.getByTestId('real'))
    expect(box).toHaveClass('is-layered')                                  // both layers are there, the placeholder over the content
    expect(box).not.toHaveClass('is-revealed')                             // the start state is painted first
    act(() => { vi.advanceTimersByTime(40) })
    expect(box).toHaveClass('is-revealed')
    expect(screen.getByTestId('sk')).toBeInTheDocument()                   // still fading out
    act(() => { vi.advanceTimersByTime(500) })
    expect(screen.queryByTestId('sk')).toBeNull()
    expect(box.dataset.state).toBe('ready')
  })
  it('keeps the content mounted through the swap, so it never restarts', () => {
    let mounts = 0
    function Probe() { mounts += 1; return <b data-testid="real" /> }
    const { rerender } = render(<Reveal ready={false} skeleton={<i />}><Probe /></Reveal>)
    rerender(<Reveal ready skeleton={<i />}><Probe /></Reveal>)
    act(() => { vi.advanceTimersByTime(600) })
    expect(screen.getByTestId('real')).toBeInTheDocument()
    expect(mounts).toBeGreaterThan(0)
    const first = screen.getByTestId('real')
    act(() => { vi.advanceTimersByTime(600) })
    expect(screen.getByTestId('real')).toBe(first)
  })
})
