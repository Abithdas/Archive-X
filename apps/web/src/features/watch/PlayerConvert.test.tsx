import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Player } from './Player'

const convert = { url: (t: number) => `/convert?t=${t}`, info: '/convert/info' }
const opened = (src = '/raw') => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ duration: 600 }), { headers: { 'Content-Type': 'application/json' } })))
  const r = render(<Player src={src} convert={convert} onUnplayable={undefined} />)
  return { ...r, video: () => r.container.querySelector('video')! }
}
afterEach(() => vi.unstubAllGlobals())

describe('Player converts what the web view cannot decode', () => {
  it('switches to the converted stream, from the start, when the video cannot be played', () => {
    const { video } = opened()
    expect(video().getAttribute('src')).toBe('/raw')
    fireEvent.error(video())
    expect(video().getAttribute('src')).toBe('/convert?t=0')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('is unplayable only when the converted stream fails too', () => {
    const { video } = opened()
    fireEvent.error(video())
    fireEvent.error(video())
    expect(screen.getByRole('alert')).toHaveTextContent('could not be played')
  })

  it('shows the real length and shifts the clock by where the converted stream starts, and a far seek starts a new conversion there', async () => {
    const { video, container } = opened()
    fireEvent.error(video())
    await waitFor(() => expect(container.textContent).toContain('10:00'))   // the length comes from the server: a live stream cannot tell it
    Object.defineProperty(video(), 'currentTime', { value: 5, configurable: true, writable: true })
    fireEvent.timeUpdate(video())
    expect(container.textContent).toContain('0:05')
    // a jump beyond what is converted reloads the converted stream at that second
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(video().getAttribute('src')).toMatch(/^\/convert\?t=/)
  })

  it('starts where the video stopped when the original fails midway', () => {
    const { video } = opened()
    Object.defineProperty(video(), 'currentTime', { value: 90, configurable: true, writable: true })
    fireEvent.error(video())
    expect(video().getAttribute('src')).toBe('/convert?t=90')
  })

  it('goes back to the original stream for the next video', () => {
    const { video, rerender } = opened()
    fireEvent.error(video())
    rerender(<Player src="/raw2" convert={convert} />)
    expect(video().getAttribute('src')).toBe('/raw2')
  })
})
