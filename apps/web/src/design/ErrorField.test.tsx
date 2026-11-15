import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorField, shakeMs } from './ErrorField'

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(0), 16))
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })   // unmount while the stubs are still in place

const input = (c: HTMLElement) => c.querySelector('.t-input') as HTMLElement
const wrap = (c: HTMLElement) => c.querySelector('.t-input-wrap') as HTMLElement

describe('shakeMs', () => it('is the four legs of the shake (80 + 80 + 60 + 60 ms by default)', () => expect(shakeMs()).toBe(280)))

describe('ErrorField', () => {
  it('is quiet until there is a message', () => {
    const { container } = render(<ErrorField message=""><input aria-label="Password" /></ErrorField>)
    expect(wrap(container).classList.contains('is-error')).toBe(false)
    expect(input(container).classList.contains('is-shaking')).toBe(false)
  })
  it('shakes when a message arrives, shows it, rings the field, and stops shaking by itself', () => {
    const { container, rerender } = render(<ErrorField message=""><input aria-label="Password" /></ErrorField>)
    rerender(<ErrorField message="Wrong password"><input aria-label="Password" /></ErrorField>)
    expect(wrap(container).classList.contains('is-error')).toBe(true)
    expect(input(container).classList.contains('is-error')).toBe(true)
    expect(screen.getByRole('alert')).toHaveTextContent('Wrong password')
    act(() => { vi.advanceTimersByTime(30) })
    expect(input(container).classList.contains('is-shaking')).toBe(true)
    act(() => { vi.advanceTimersByTime(400) })
    expect(input(container).classList.contains('is-shaking')).toBe(false)       // the shake is over ...
    expect(input(container).classList.contains('is-error')).toBe(true)          // ... the error stays until the parent clears it
  })
  it('shakes again for the same message when replayed', () => {
    const { container, rerender } = render(<ErrorField message="Wrong password" replay={0}><input aria-label="P" /></ErrorField>)
    act(() => { vi.advanceTimersByTime(500) })
    expect(input(container).classList.contains('is-shaking')).toBe(false)
    rerender(<ErrorField message="Wrong password" replay={1}><input aria-label="P" /></ErrorField>)
    act(() => { vi.advanceTimersByTime(30) })
    expect(input(container).classList.contains('is-shaking')).toBe(true)
  })
  it('keeps the words while the message fades out, and drops the ring at once', () => {
    const { container, rerender } = render(<ErrorField message="Wrong password"><input aria-label="P" /></ErrorField>)
    rerender(<ErrorField message=""><input aria-label="P" /></ErrorField>)
    expect(wrap(container).classList.contains('is-error')).toBe(false)
    expect(container.querySelector('.t-error-msg')?.textContent).toBe('Wrong password')   // still painted for the fade (CSS hides it after)
    expect(input(container).classList.contains('is-shaking')).toBe(false)
  })
  it('leaves the message out of the field when quiet, but still shakes', () => {
    const { container, rerender } = render(<ErrorField quiet message=""><input aria-label="Code" /></ErrorField>)
    rerender(<ErrorField quiet message="Error: bad code"><input aria-label="Code" /></ErrorField>)
    expect(container.querySelector('.t-error-msg')).toBeNull()
    expect(wrap(container).classList.contains('is-error')).toBe(true)
  })
  it('does not change what the input is called: a label next to it is not polluted by the message', () => {
    render(<div><label htmlFor="p">Password</label><ErrorField message="Wrong password"><input id="p" /></ErrorField></div>)
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
  })
})
