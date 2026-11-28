import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Player, nextRate } from './Player'
import { enterFullscreen } from './fullscreen'
import { Timeline } from './Timeline'
import { telegramLink } from './WatchPage'

describe('Player skip button', () => {
  const skips = [{ type: 'intro' as const, start: 30, end: 90 }, { type: 'credits' as const, start: 500, end: null }]
  const at = (v: HTMLVideoElement, t: number) => { Object.defineProperty(v, 'currentTime', { value: t, configurable: true, writable: true }); fireEvent.timeUpdate(v) }
  it('shows Skip intro inside the opening and jumps to its end', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} skips={skips} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'duration', { value: 600, configurable: true })
    at(v, 10)
    expect(screen.queryByTestId('skip-button')).toBeNull()
    at(v, 45)
    fireEvent.click(screen.getByRole('button', { name: 'Skip intro' }))
    expect(v.currentTime).toBe(90)
  })
  it('offers Next episode in the credits when there is a next one', () => {
    const next = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} skips={skips} onNext={next} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'duration', { value: 600, configurable: true })
    at(v, 520)
    fireEvent.click(screen.getByRole('button', { name: 'Next episode' }))
    expect(next).toHaveBeenCalledTimes(1)
  })
})

describe('Player seek requests (scene-marker chips)', () => {
  it('seeks without starting playback, and seeks again on every new request', () => {
    const { container, rerender } = render(<Player src="/stream/-1/1" durationHint={600} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    const play = vi.fn(() => Promise.resolve()); v.play = play as never
    Object.defineProperty(v, 'duration', { value: 600, configurable: true })
    Object.defineProperty(v, 'readyState', { value: 1, configurable: true })
    rerender(<Player src="/stream/-1/1" durationHint={600} seekRequest={{ t: 120, n: 1 }} onOpenVlc={() => {}} />)
    expect(v.currentTime).toBe(120)
    rerender(<Player src="/stream/-1/1" durationHint={600} seekRequest={{ t: 120, n: 2 }} onOpenVlc={() => {}} />)   // same time again after scrubbing away
    v.currentTime = 300
    rerender(<Player src="/stream/-1/1" durationHint={600} seekRequest={{ t: 120, n: 3 }} onOpenVlc={() => {}} />)
    expect(v.currentTime).toBe(120)
    expect(play).not.toHaveBeenCalled()
  })
  it('waits for the video to know its length', () => {
    const { container, rerender } = render(<Player src="/stream/-1/1" durationHint={600} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'readyState', { value: 0, configurable: true })
    rerender(<Player src="/stream/-1/1" durationHint={600} seekRequest={{ t: 200, n: 1 }} onOpenVlc={() => {}} />)
    expect(v.currentTime).toBe(0)
    Object.defineProperty(v, 'duration', { value: 600, configurable: true })
    fireEvent.loadedMetadata(v)
    expect(v.currentTime).toBe(200)
  })
})

describe('Player releases the video when the viewer leaves', () => {
  const calls = (f: ReturnType<typeof vi.fn>) => f.mock.calls as unknown as Array<[string, RequestInit]>
  const releases = (f: ReturnType<typeof vi.fn>) => calls(f).filter((c) => c[0] === '/api/play/release').map((c) => JSON.parse(String(c[1].body)))
  it('tells the server when the page is left, when another video starts, and when the tab closes', () => {
    const f = vi.fn(async () => new Response('{}'))
    vi.stubGlobal('fetch', f)
    const media = (mid: number) => ({ cid: '-1001', mid })
    const { rerender, unmount } = render(<Player media={media(5)} src="/stream/-1001/5" durationHint={60} onOpenVlc={() => {}} />)
    expect(releases(f)).toEqual([])
    rerender(<Player media={media(6)} src="/stream/-1001/6" durationHint={60} onOpenVlc={() => {}} />)
    expect(releases(f)).toEqual([{ channel_id: -1001, message_id: 5, reason: 'viewer_left' }])
    window.dispatchEvent(new Event('pagehide'))
    expect(releases(f).length).toBe(2)
    unmount()
    expect(releases(f).map((r) => r.message_id)).toEqual([5, 6, 6])
    expect(calls(f).at(-1)![1].keepalive).toBe(true)
    vi.unstubAllGlobals()
  })
  it('does nothing for a video that is not from Telegram', () => {
    const f = vi.fn(async () => new Response('{}'))
    vi.stubGlobal('fetch', f)
    render(<Player src="/stream/x/1" durationHint={60} onOpenVlc={() => {}} />).unmount()
    expect(releases(f)).toEqual([])
    vi.unstubAllGlobals()
  })
})

describe('Player resume hooks', () => {
  const metadata = (video: HTMLVideoElement, duration: number) => {
    Object.defineProperty(video, 'duration', { value: duration, configurable: true })
    fireEvent.loadedMetadata(video)
  }
  it('starts at the chosen position once the video knows its length, and not again after a retry', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} startAt={300} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    metadata(v, 600)
    expect(v.currentTime).toBe(300)
    v.currentTime = 450
    metadata(v, 600)   // e.g. a second loadedmetadata after a stall: must not jump back
    expect(v.currentTime).toBe(450)
  })
  it('never starts past the end', () => {
    const { container } = render(<Player src="/stream/-1/1" startAt={9999} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    metadata(v, 600)
    expect(v.currentTime).toBeLessThanOrEqual(600)
  })
  it('reports time, play and the end', () => {
    const onTime = vi.fn(), onPlayed = vi.fn(), onEnd = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} onTime={onTime} onPlayed={onPlayed} onEnd={onEnd} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    v.currentTime = 42
    fireEvent.timeUpdate(v)
    expect(onTime).toHaveBeenCalledWith(42, 600)
    fireEvent.play(v)
    expect(onPlayed).toHaveBeenCalledTimes(1)
    fireEvent.ended(v)
    expect(onEnd).toHaveBeenCalledTimes(1)
  })
  it('does not start at 0 seconds specially', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    v.currentTime = 12
    metadata(v, 600)
    expect(v.currentTime).toBe(12)
  })
})

describe('Player', () => {
  it('shows each time once (no duplicate "0:00 / 2:00" copy)', () => {
    render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={() => {}} />)
    expect(screen.getAllByText('0:00')).toHaveLength(1)
    expect(screen.getAllByText('2:00')).toHaveLength(1)
    expect(screen.queryByText('0:00 / 2:00')).not.toBeInTheDocument()
  })
  it('offers retry and VLC when the stream cannot be played', () => {
    const vlc = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={vlc} />)
    fireEvent.error(container.querySelector('video')!)
    expect(screen.getByRole('alert')).toHaveTextContent('could not be played')
    fireEvent.click(screen.getByRole('button', { name: 'Open in VLC' }))
    expect(vlc).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry original' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('offers the native player first when the browser cannot play the file (desktop app)', () => {
    const native = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={() => {}} onOpenNative={native} />)
    fireEvent.error(container.querySelector('video')!)
    fireEvent.click(screen.getByRole('button', { name: 'Play with the native player' }))
    expect(native).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Retry original' })).toBeInTheDocument()
  })
  it('tells the page once when the file cannot be played, so it can switch to the native player by itself', () => {
    const unplayable = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={() => {}} onUnplayable={unplayable} />)
    expect(unplayable).not.toHaveBeenCalled()
    fireEvent.error(container.querySelector('video')!)
    expect(unplayable).toHaveBeenCalledTimes(1)
  })
  it('never converts a file it cannot decode: a decode error is a failure, handed on, with no conversion request', () => {
    const asked: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { asked.push(String(u)); return new Response('{}', { status: 404 }) }))
    const unplayable = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={120} onOpenVlc={() => {}} onUnplayable={unplayable} />)
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'error', { value: { code: 4 }, configurable: true })
    fireEvent.error(v)
    expect(screen.getByRole('alert')).toHaveTextContent('could not be played')
    expect(unplayable).toHaveBeenCalledTimes(1)
    expect(asked.some((u) => /hls|smart|convert/i.test(u))).toBe(false)
    expect(screen.queryByText(/Converting/i)).toBeNull()
    vi.unstubAllGlobals()
  })
  it('does not mention the native player where there is none (browser)', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={() => {}} />)
    fireEvent.error(container.querySelector('video')!)
    expect(screen.queryByRole('button', { name: 'Play with the native player' })).toBeNull()
  })
  it('disables previous and next when there is nothing to go to', () => {
    render(<Player src="/stream/-1/1" onOpenVlc={() => {}} />)
    expect(screen.getByRole('button', { name: 'Previous video' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next video' })).toBeDisabled()
  })
  it('cycles the playback speed', () => {
    expect([nextRate(1), nextRate(2), nextRate(0.5)]).toEqual([1.25, 0.5, 0.75])
  })
})

// jsdom has no PointerEvent; a MouseEvent subclass carries the coordinates the component reads.
if (!('PointerEvent' in window)) {
  class PE extends MouseEvent { pointerId = 1; pointerType = 'mouse' }
  Object.defineProperty(window, 'PointerEvent', { value: PE })
}

describe('Timeline', () => {
  const rect = { left: 0, width: 200, top: 0, height: 28, right: 200, bottom: 28, x: 0, y: 0, toJSON: () => ({}) }
  it('exposes slider semantics and seeks with the keyboard', () => {
    const seeks: number[] = []
    render(<Timeline time={50} duration={100} buffered={60} segments={[]} onSeek={(t) => seeks.push(t)} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    expect(s).toHaveAttribute('aria-valuenow', '50')
    expect(s).toHaveAttribute('aria-valuetext', '0:50 of 1:40')
    fireEvent.keyDown(s, { key: 'ArrowRight' })
    fireEvent.keyDown(s, { key: 'End' })
    expect(seeks).toEqual([55, 100])
  })
  it('is disabled until the duration is known', () => {
    const seeks: number[] = []
    render(<Timeline time={0} duration={0} buffered={0} segments={[]} onSeek={(t) => seeks.push(t)} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    expect(s).toHaveAttribute('aria-disabled', 'true')
    fireEvent.keyDown(s, { key: 'ArrowRight' })
    expect(seeks).toEqual([])
  })
  it('draws one segment per chapter and shows the time and scene on hover', () => {
    const segs = [{ start: 0, end: 40, title: 'Opening', intro: true }, { start: 40, end: 100, title: 'Main', intro: false }]
    render(<Timeline time={0} duration={100} buffered={0} segments={segs} onSeek={() => {}} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    s.getBoundingClientRect = () => rect as DOMRect
    expect(document.querySelectorAll('[data-segment]')).toHaveLength(2)
    fireEvent.pointerMove(s, { clientX: 150 })
    expect(screen.getByText('Main')).toBeInTheDocument()
    expect(screen.getByText('1:15')).toBeInTheDocument()
  })
  it('tells when the hover picture comes and goes, and always draws it above the bar', () => {
    const tips: boolean[] = []
    render(<Timeline time={0} duration={100} buffered={0} segments={[]} onSeek={() => {}} onTip={(on) => tips.push(on)} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    s.getBoundingClientRect = () => rect as DOMRect
    fireEvent.pointerMove(s, { clientX: 150 })
    expect(tips.at(-1)).toBe(true)
    expect((screen.getByText('1:15').closest('div[class*="bottom-full"]'))).not.toBeNull()
    fireEvent.pointerLeave(s)
    expect(tips.at(-1)).toBe(false)
  })
})

describe('telegramLink', () => {
  it('builds a t.me link for -100 channel ids only', () => {
    expect(telegramLink('-5002345', 7)).toBeNull()
    expect(telegramLink('-1001234567890', 7)).toBe('https://t.me/c/1234567890/7')
  })
})

describe('Player control deck (minimal player look)', () => {
  const mount = () => render(<Player src="/stream/-1/1" durationHint={600} onOpenVlc={() => {}} />)
  it('the speed menu lists every speed, ticks the current one and applies a choice', () => {
    const { container } = mount()
    const v = container.querySelector('video')!
    fireEvent.click(screen.getByRole('button', { name: 'Speed 1x' }))
    const items = screen.getAllByRole('menuitemradio').map((i) => i.textContent)
    expect(items).toEqual(['0.5x', '0.75x', '1x✓', '1.25x', '1.5x', '2x'])
    fireEvent.click(screen.getByRole('menuitemradio', { name: '1.5x' }))
    expect(v.playbackRate).toBe(1.5)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()   // closes after a choice
    expect(screen.getByRole('button', { name: 'Speed 1.5x' })).toBeInTheDocument()
  })
  it('the settings menu has no skip items, and toggles auto-repeat', () => {
    const { container } = mount()
    const v = container.querySelector('video')!
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.queryByRole('menuitem', { name: /Forward 30|Back 30/ })).toBeNull()
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: 'Loop' }))
    expect(v.loop).toBe(true)
  })
  it('menus close with Escape or a click elsewhere, and only one is open at a time', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Speed 1x' }))
    expect(screen.getByRole('menu', { name: 'Speed 1x' })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
  it('the buffer menu reports how much is buffered', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Buffer status' }))
    expect(screen.getByRole('menu', { name: 'Buffer status' })).toHaveTextContent('Buffered0%')
  })
  it('the volume slider and mute button work', () => {
    const { container } = mount()
    const v = container.querySelector('video')!
    const vol = screen.getByRole('slider', { name: 'Volume' })
    expect(vol).toHaveAttribute('aria-valuenow', '100')
    fireEvent.keyDown(vol, { key: 'ArrowLeft' })
    expect(screen.getByRole('slider', { name: 'Volume' })).toHaveAttribute('aria-valuenow', '95')
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    expect(v.muted).toBe(true)
    expect(screen.getByRole('slider', { name: 'Volume' })).toHaveAttribute('aria-valuenow', '0')
  })
})

describe('Timeline live seek preview', () => {
  const rect = { left: 0, width: 200, top: 0, height: 28, right: 200, bottom: 28, x: 0, y: 0, toJSON: () => ({}) }
  const RealImage = globalThis.Image
  let loaded: Array<{ src: string; ok: () => void; fail: () => void }> = []
  const preview = { times: [10, 40, 70, 100], url: (n: number) => `/snap/${n}`, live: (t: number) => `/live/${t}` }
  const setup = (seeks: number[]) => {
    loaded = []
    globalThis.Image = class { onload?: () => void; onerror?: () => void; decoding = ''
      set src(v: string) { const me = this; loaded.push({ src: v, ok: () => me.onload?.(), fail: () => me.onerror?.() }) } } as unknown as typeof Image
    render(<Timeline time={0} duration={100} buffered={0} segments={[]} preview={preview} onSeek={(t) => seeks.push(t)} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    s.getBoundingClientRect = () => rect as DOMRect
    s.setPointerCapture = () => {}
    return s
  }
  afterEach(() => { globalThis.Image = RealImage })

  it('waits with a spinner, shows the frame of that very second once it arrives, and labels it with that second', async () => {
    const s = setup([])
    fireEvent.pointerMove(s, { clientX: 150 })   // 75 s
    expect(screen.getByText('1:15')).toBeInTheDocument()
    expect(document.querySelector('img[src^="/snap/"]')).toBeNull()   // no picture from somewhere else while waiting
    await waitFor(() => expect(loaded.map((l) => l.src)).toContain('/live/75'))
    expect(screen.getByRole('status', { name: 'Loading preview' })).toBeInTheDocument()
    loaded.find((l) => l.src === '/live/75')!.ok()
    await waitFor(() => expect(document.querySelector('img[src="/live/75"]')).not.toBeNull())
    expect(screen.queryByRole('status', { name: 'Loading preview' })).not.toBeInTheDocument()
    expect(screen.getByText('1:15')).toBeInTheDocument()
  })
  it('a click seeks to the second that was previewed', async () => {
    const seeks: number[] = []
    const s = setup(seeks)
    fireEvent.pointerDown(s, { clientX: 150, pointerId: 1 })
    fireEvent.pointerUp(s, { clientX: 150, pointerId: 1 })
    expect(seeks).toEqual([75])
  })
  it('moving to another second asks for that second, not the old one', async () => {
    const s = setup([])
    fireEvent.pointerMove(s, { clientX: 100 })   // 50 s
    await waitFor(() => expect(loaded.map((l) => l.src)).toContain('/live/50'))
    fireEvent.pointerMove(s, { clientX: 110 })   // 55 s
    await waitFor(() => expect(loaded.map((l) => l.src)).toContain('/live/55'))
    expect(screen.getByText('0:55')).toBeInTheDocument()
  })
  it('when the exact frame cannot be made it shows the nearest stored snapshot with that snapshot\'s own time, and a click seeks there', async () => {
    const seeks: number[] = []
    const s = setup(seeks)
    fireEvent.pointerMove(s, { clientX: 90 })   // 45 s -> nearest snapshot is at 40 s
    await waitFor(() => expect(loaded.map((l) => l.src)).toContain('/live/45'))
    loaded.find((l) => l.src === '/live/45')!.fail()
    await waitFor(() => expect(document.querySelector('img[src="/snap/1"]')).not.toBeNull())
    expect(screen.getByText('0:40')).toBeInTheDocument()   // the label is the snapshot's time, not the pointer's
    fireEvent.pointerDown(s, { clientX: 90, pointerId: 1 })
    fireEvent.pointerUp(s, { clientX: 90, pointerId: 1 })
    expect(seeks).toEqual([40])
  })
})

describe('Player frame shape and full screen', () => {
  it('takes the video\'s own aspect ratio instead of a fixed 16:9 frame', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={60} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'duration', { value: 60, configurable: true })
    Object.defineProperty(v, 'videoWidth', { value: 1080, configurable: true }); Object.defineProperty(v, 'videoHeight', { value: 1920, configurable: true })
    fireEvent.loadedMetadata(v)
    expect((v.parentElement as HTMLElement).style.aspectRatio).toMatch(/^0\.5/)
    Object.defineProperty(v, 'videoWidth', { value: 1920, configurable: true }); Object.defineProperty(v, 'videoHeight', { value: 800, configurable: true })
    fireEvent.loadedMetadata(v)
    expect(Number((v.parentElement as HTMLElement).style.aspectRatio)).toBeCloseTo(2.4, 1)
  })
  it('fills the screen in full screen and puts the controls over the picture', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={60} onOpenVlc={() => {}} />)
    const box = container.firstElementChild as HTMLElement
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    act(() => enterFullscreen())
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
    expect(box.className).toContain('h-full')
    expect(box.getAttribute('popover')).toBe('manual')                       // shown in the top layer, over whatever the page is doing
    expect((container.querySelector('video')!.parentElement as HTMLElement).className).toContain('inset-0')
    expect(box.querySelector('[class*="bottom-0"]')).not.toBeNull()
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
    expect(box.className).not.toContain('h-full')
    expect(box.getAttribute('popover')).toBeNull()
  })
  it('shows no name of the video or file in full screen', () => {
    const { container } = render(<Player src="/stream/-1/The.Matrix.1999.1080p.mkv" durationHint={60} onOpenVlc={() => {}} />)
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    act(() => enterFullscreen())
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
    expect(screen.queryByTestId('player-title')).toBeNull()
    expect(container.textContent).not.toMatch(/Matrix|\.mkv/)
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
  })
  it('the next video is still in full screen: the page stays in full screen while the player is replaced', () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    const first = render(<Player src="/stream/-1/1" durationHint={60} onOpenVlc={() => {}} />)
    act(() => enterFullscreen())
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
    first.unmount()                                                           // the page swaps the player out while the next video loads
    const next = render(<Player src="/stream/-1/2" durationHint={60} onOpenVlc={() => {}} />)
    expect((next.container.firstElementChild as HTMLElement).className).toContain('h-full')
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
  })
})
