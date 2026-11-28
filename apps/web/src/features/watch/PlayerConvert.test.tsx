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

  describe('a video that plays without any sound', () => {
    const playing = (v: HTMLVideoElement, audioBytes: number | undefined, at = 5) => {
      Object.defineProperty(v, 'paused', { value: false, configurable: true })
      Object.defineProperty(v, 'currentTime', { value: at, configurable: true, writable: true })
      if (audioBytes !== undefined) { Object.defineProperty(v, 'webkitAudioDecodedByteCount', { value: audioBytes, configurable: true }); Object.defineProperty(v, 'webkitVideoDecodedByteCount', { value: 900000, configurable: true }) }
      fireEvent.timeUpdate(v)
    }
    it('is converted from where it is when the picture plays and no sound was ever decoded', () => {
      const { video } = opened()
      playing(video(), 0)
      expect(video().getAttribute('src')).toBe('/convert?t=5')
    })
    it('is left alone when sound is being decoded, or the browser does not say', () => {
      const a = opened(); playing(a.video(), 48000); expect(a.video().getAttribute('src')).toBe('/raw'); a.unmount()
      const b = opened(); playing(b.video(), undefined); expect(b.video().getAttribute('src')).toBe('/raw')
    })
    it('waits for the first seconds, and never checks a muted video', () => {
      const a = opened(); playing(a.video(), 0, 1); expect(a.video().getAttribute('src')).toBe('/raw'); a.unmount()
      const b = opened(); Object.defineProperty(b.video(), 'muted', { value: true, configurable: true }); playing(b.video(), 0); expect(b.video().getAttribute('src')).toBe('/raw')
    })
    it('hands over to the other engine when it cannot convert, and with silentOnly a failure is not converted', () => {
      const away = vi.fn()
      const { container } = render(<Player src="/raw" onUnplayable={away} />)
      playing(container.querySelector('video')!, 0)
      expect(away).toHaveBeenCalledTimes(1)
      const other = render(<Player src="/raw2" convert={{ ...convert, silentOnly: true }} />)
      const v = other.container.querySelector('video')!
      fireEvent.error(v)
      expect(v.getAttribute('src')).toBe('/raw2')
      expect(screen.getAllByRole('alert').length).toBeGreaterThan(0)
    })
  })

  describe('a video whose picture is never decoded (sound only, a black screen)', () => {
    const black = (v: HTMLVideoElement, videoBytes: number | undefined, at = 2) => {
      Object.defineProperty(v, 'paused', { value: false, configurable: true })
      Object.defineProperty(v, 'currentTime', { value: at, configurable: true, writable: true })
      Object.defineProperty(v, 'muted', { value: true, configurable: true })   // the silent check leaves a muted video alone, so only this check is looked at
      if (videoBytes !== undefined) Object.defineProperty(v, 'webkitVideoDecodedByteCount', { value: videoBytes, configurable: true })
      fireEvent.timeUpdate(v)
    }
    it('is converted from where it is', () => {
      const { video } = opened()
      black(video(), 0)
      expect(video().getAttribute('src')).toBe('/convert?t=2')
    })
    it('is left alone while a picture is decoded, when the browser does not say, or in the first second', () => {
      const a = opened(); black(a.video(), 120000); expect(a.video().getAttribute('src')).toBe('/raw'); a.unmount()
      const b = opened(); black(b.video(), undefined); expect(b.video().getAttribute('src')).toBe('/raw'); b.unmount()
      const c = opened(); black(c.video(), 0, 0.5); expect(c.video().getAttribute('src')).toBe('/raw')
    })
    it('hands over to the native player when it cannot be converted here', () => {
      const away = vi.fn()
      const { container } = render(<Player src="/raw" onUnplayable={away} />)
      black(container.querySelector('video')!, 0)
      expect(away).toHaveBeenCalledTimes(1)
    })
  })
})
