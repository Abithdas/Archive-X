import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Player } from './Player'
import type { PlayInfo, SmartApi } from './smartstream'

const loader = vi.hoisted(() => ({ load: vi.fn() }))
vi.mock('./smartstream', async (orig) => ({ ...(await orig<typeof import('./smartstream')>()), loadSmartStream: loader.load }))

const NEEDS: PlayInfo = { direct_likely: false, name: 'a.mkv', vcodec: 'hevc', acodec: 'aac' }
const mkSmart = (over: Partial<SmartApi> = {}) => {
  const destroy = vi.fn()
  const start = vi.fn((..._a: unknown[]) => ({ destroy }))
  const api: SmartApi = { canPlay: () => true, start: start as never, ...over }
  return { api, start, destroy }
}
const hls = vi.hoisted(() => ({ start: vi.fn() }))
vi.mock('./hlsplay', async (orig) => ({ ...(await orig<typeof import('./hlsplay')>()), startHls: hls.start }))
beforeEach(() => { loader.load.mockReset(); hls.start.mockReset(); hls.start.mockResolvedValue({ destroy: vi.fn() }) })
afterEach(() => vi.useRealTimers())
const video = (c: HTMLElement) => c.querySelector('video')!
const decodeError = (v: HTMLVideoElement, code = 3) => { Object.defineProperty(v, 'error', { value: { code }, configurable: true }); fireEvent.error(v) }

describe('Player smart playback', () => {
  it('plays directly when the file is expected to play, and never loads the engine', () => {
    const { container } = render(<Player info={{ direct_likely: true }} src="/stream/-1/1" onOpenVlc={() => {}} />)
    expect(video(container).getAttribute('src')).toBe('/stream/-1/1'); expect(loader.load).not.toHaveBeenCalled()
  })
  it('waits for the codec facts before giving the video its source', async () => {
    const { container, rerender } = render(<Player info={undefined} infoPending src="/stream/-1/1" onOpenVlc={() => {}} />)
    expect(video(container).hasAttribute('src')).toBe(false)
    rerender(<Player info={{ direct_likely: true }} infoPending={false} src="/stream/-1/1" onOpenVlc={() => {}} />)
    await waitFor(() => expect(video(container).getAttribute('src')).toBe('/stream/-1/1'))
  })
  it('hands a file that needs it to the smart engine, with the resume position, and cleans up', async () => {
    const { api, start, destroy } = mkSmart(); loader.load.mockResolvedValue(api)
    const { container, unmount } = render(<Player info={NEEDS} src="/stream/-1/1" startAt={90} onOpenVlc={() => {}} />)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    const [el, url, info, opts] = start.mock.calls[0] as [HTMLVideoElement, string, PlayInfo, { startTime: number }]
    expect(el).toBe(video(container)); expect(url).toBe('/stream/-1/1'); expect(info).toBe(NEEDS); expect(opts.startTime).toBe(90)
    expect(video(container).hasAttribute('src')).toBe(false)
    unmount(); expect(destroy).toHaveBeenCalled()
  })
  it('converts on the PC (HLS) when the engine reports an error, and says so if that fails too', async () => {
    const { api, start } = mkSmart(); loader.load.mockResolvedValue(api)
    render(<Player info={NEEDS} src="/stream/-1/1" onOpenVlc={() => {}} />)
    await waitFor(() => expect(start).toHaveBeenCalled())
    const opts = start.mock.calls[0]![3] as { onError: (m: string) => void }
    act(() => opts.onError('boom'))
    await waitFor(() => expect(hls.start).toHaveBeenCalledTimes(1))
    expect(hls.start.mock.calls[0]![1]).toBe('/hls/-1/1/index.m3u8')
    act(() => (hls.start.mock.calls[0]![2] as { onError: (w: string) => void }).onError('x'))
    expect(await screen.findByText(/could not be converted for the browser/)).toBeInTheDocument()
  })
  it('falls back to direct play when the engine has no pipeline for the file', async () => {
    const { api, start } = mkSmart({ start: vi.fn(() => null) as never }); loader.load.mockResolvedValue(api)
    const { container } = render(<Player info={NEEDS} src="/stream/-1/1" onOpenVlc={() => {}} />)
    await waitFor(() => expect(video(container).getAttribute('src')).toBe('/stream/-1/1'))
    expect(start).not.toHaveBeenCalled()
  })
  it('tries the smart engine once, at the same position, when direct play cannot decode', async () => {
    const { api, start } = mkSmart(); loader.load.mockResolvedValue(api)
    const { container } = render(<Player info={NEEDS} src="/stream/-1/1" onOpenVlc={() => {}} />)
    // an engine without a pipeline first: stay direct, then a decode error must not loop back into it
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    const first = video(container)
    expect(first.hasAttribute('src')).toBe(false)
  })
  it('switches to the smart engine after a decode failure on a directly started video', async () => {
    const { api, start } = mkSmart({ canPlay: (i: PlayInfo) => !i.direct_likely }); loader.load.mockResolvedValue(api)
    const { container, rerender } = render(<Player info={{ direct_likely: true }} src="/stream/-1/1" onOpenVlc={() => {}} />)
    const v = video(container)
    expect(v.getAttribute('src')).toBe('/stream/-1/1'); Object.defineProperty(v, 'currentTime', { value: 42, writable: true, configurable: true })
    rerender(<Player info={{ ...NEEDS, direct_likely: true }} src="/stream/-1/1" onOpenVlc={() => {}} />)   // facts say it should play, so no engine yet
    decodeError(v)
    await waitFor(() => expect(hls.start).toHaveBeenCalledTimes(1)); expect(start).not.toHaveBeenCalled()
  })
  it('a direct decode failure on a file the engine can take starts it from where it stopped, once', async () => {
    const { api, start } = mkSmart({ canPlay: () => true }); loader.load.mockResolvedValue(api)
    // direct_likely true gives no engine up front; a later "not likely" fact plus a decode error does
    const info: PlayInfo = { direct_likely: false, name: 'a.mp4' }
    const { container, rerender } = render(<Player info={undefined} infoPending={false} src="/stream/-1/1" onOpenVlc={() => {}} />)
    const v = video(container)
    expect(v.getAttribute('src')).toBe('/stream/-1/1'); Object.defineProperty(v, 'currentTime', { value: 42, writable: true, configurable: true })
    rerender(<Player info={info} src="/stream/-1/1" onOpenVlc={() => {}} />)
    await waitFor(() => expect(loader.load).toHaveBeenCalled())
    decodeError(v)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect((start.mock.calls[0]![3] as { startTime: number }).startTime).toBe(42)
    expect(screen.getByText('Trying client-side smart playback…')).toBeInTheDocument()
    decodeError(video(container))   // fails again on the smart path: the PC converts it instead, once
    await waitFor(() => expect(hls.start).toHaveBeenCalledTimes(1)); expect(start).toHaveBeenCalledTimes(1)
    decodeError(video(container))   // and that is final
    expect(await screen.findByText(/could not be converted for the browser/)).toBeInTheDocument()
  })
  it('without the engine, a decode error is converted on the PC; if that is unavailable the message offers Retry and VLC', async () => {
    loader.load.mockResolvedValue(null)
    const { container } = render(<Player info={{ direct_likely: false }} src="/stream/-1/1" onOpenVlc={() => {}} />)
    await waitFor(() => expect(video(container).getAttribute('src')).toBe('/stream/-1/1'))
    decodeError(video(container))
    await waitFor(() => expect(hls.start).toHaveBeenCalledTimes(1))
    expect(hls.start.mock.calls[0]![1]).toBe('/hls/-1/1/index.m3u8')
    act(() => (hls.start.mock.calls[0]![2] as { onError: (w: string) => void }).onError('x'))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry original' })).toBeInTheDocument()
  })
})
