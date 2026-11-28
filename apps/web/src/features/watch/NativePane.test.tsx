import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enterFullscreen, leaveFullscreen } from './fullscreen'
import { NativePane } from './NativePane'

type Call = { command: string; args: Record<string, unknown> | undefined }
let calls: Call[]
let state: Record<string, unknown>
let overrides: Record<string, () => unknown>
const SIGNED = 'http://127.0.0.1:18570/video/5?c=-1001&t=abc'
const OPENED = { duration: 120, width: 1920, height: 1080, decoder: 'avcodec', layer: 'popup' }
const VTT = 'WEBVTT\n\n00:00:10.000 --> 00:00:20.000\nHello from the file\n'
const TRACKS = { audio: [{ index: 1, ordinal: 0, label: 'English', language: 'en', default: true }, { index: 2, ordinal: 1, label: 'Spanish', language: 'es', default: false }], subtitles: [{ index: 3, ordinal: 0, label: 'English subs', language: 'en', text: true, forced: false, default: false }], captions: { available: false, engine: '', install: '' } }

const BOX = { top: 100, bottom: 460, left: 40, right: 680, width: 640, height: 360, x: 40, y: 100, toJSON() {} }
beforeEach(() => {
  calls = []; overrides = {}
  state = { position: 12, duration: 120, playing: true, ended: false, buffering: false, width: 1920, height: 1080, error: 0, opened: true, message: '', windowVisible: true, windowWidth: 640, windowHeight: 360, layer: 'popup', framesDrawn: 5, framesDropped: 0, screenDistinct: 9, screenLumaRange: 80 }
  document.body.innerHTML = ''
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} })
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(() => BOX as DOMRect)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url)
    if (u.includes('/api/play/device-link/')) return new Response(JSON.stringify({ url: SIGNED }), { status: 200 })
    if (u.includes('/api/play/tracks/')) return new Response(JSON.stringify(TRACKS), { status: 200 })
    if (u.includes('/api/play/subtitle/')) return new Response(VTT, { status: 200 })
    return new Response('{}', { status: 404 })
  }))
  ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
    invoke: vi.fn(async (command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args })
      if (overrides[command]) return overrides[command]!()
      switch (command) {
        case 'native_available': return { available: true, reason: '' }
        case 'native_open': return OPENED
        case 'native_state': return state
        case 'native_tracks': return [{ name: 'English', selected: true }, { name: 'Spanish', selected: false }]
        case 'native_command': if (args?.command === 'pause') state = { ...state, playing: false }; if (args?.command === 'play') state = { ...state, playing: true }; return undefined
        default: return undefined
      }
    }),
  }
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ })

const cmds = (name: string) => calls.filter((c) => c.command === 'native_command' && c.args?.command === name)
const mount = (props: Partial<React.ComponentProps<typeof NativePane>> = {}) => render(<NativePane media={{ cid: '-1001', mid: 5 }} durationHint={120} size={{ width: 1920, height: 1080 }} {...props} />)
const ready = async () => { await waitFor(() => expect(cmds('play')).toHaveLength(1)); await screen.findByRole('button', { name: 'Pause' }) }

describe('NativePane', () => {
  it('opens the signed stream in the native player, starts playing, and shows the same kind of controls as the page player', async () => {
    mount()
    await ready()
    expect(calls.find((c) => c.command === 'native_open')!.args).toMatchObject({ url: SIGNED, x: 40, y: 100, w: 640, h: 360 })
    expect(screen.getByTestId('native-pane')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Speed 1x' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous video' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Audio and subtitles' })).toBeInTheDocument()
  })

  it('starts at the resume position and does not play when autoplay is off', async () => {
    mount({ startAt: 45, autoPlay: false })
    await waitFor(() => expect(cmds('seek')[0]?.args?.value).toBe(45))
    expect(cmds('play')).toHaveLength(0)
  })

  it('moves the picture back to the window above the app when the engine opens it elsewhere', async () => {
    overrides.native_open = () => ({ ...OPENED, layer: 'front' })
    mount()
    await waitFor(() => expect(cmds('popup').length).toBeGreaterThan(0))
  })

  it('jumps from a marker, from the keyboard, and reports the time to the page', async () => {
    const onTime = vi.fn()
    const { rerender } = mount({ onTime })
    await ready()
    await waitFor(() => expect(onTime).toHaveBeenCalledWith(12, 120))
    rerender(<NativePane media={{ cid: '-1001', mid: 5 }} durationHint={120} onTime={onTime} seekRequest={{ t: 80, n: 1 }} />)
    await waitFor(() => expect(cmds('seek').some((c) => c.args?.value === 80)).toBe(true))
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() => expect(cmds('seek').some((c) => c.args?.value === 17)).toBe(true))      // 12 + 5, the browser player's step
    fireEvent.keyDown(window, { key: 'l' })
    await waitFor(() => expect(cmds('seek').some((c) => c.args?.value === 42)).toBe(true))      // 12 + 30
  })

  it('shows the captions of a subtitle track in a strip under the picture, at the engine clock', async () => {
    state = { ...state, position: 12 }
    mount()
    await ready()
    expect(screen.queryByTestId('captions')).toBeNull()                                           // off: no empty strip
    fireEvent.click(await screen.findByRole('button', { name: 'Audio and subtitles' }))
    fireEvent.click(await screen.findByRole('menuitemradio', { name: 'English subs' }))
    const strip = await screen.findByTestId('captions')
    await waitFor(() => expect(within(strip).getByText('Hello from the file')).toBeInTheDocument())
    state = { ...state, position: 40 }                                                            // past the cue
    await waitFor(() => expect(within(screen.getByTestId('captions')).queryByText('Hello from the file')).toBeNull())
  })

  it('chooses the audio track by the file\'s own order', async () => {
    mount()
    await ready()
    fireEvent.click(await screen.findByRole('button', { name: 'Audio and subtitles' }))
    fireEvent.click(await screen.findByRole('menuitemradio', { name: 'Spanish' }))
    await waitFor(() => expect(cmds('audio')[0]?.args?.value).toBe(1))
  })

  it('changes speed and volume through the engine', async () => {
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Speed 1x' }))
    fireEvent.click(await screen.findByRole('menuitemradio', { name: '1.5x' }))
    await waitFor(() => expect(cmds('rate')[0]?.args?.value).toBe(1.5))
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    await waitFor(() => expect(cmds('mute').some((c) => c.args?.value === 1)).toBe(true))
  })

  it('steps the picture aside while a dialog is open and brings it back after', async () => {
    mount()
    await ready()
    await waitFor(() => expect(calls.some((c) => c.command === 'native_rect')).toBe(true))
    const dialog = document.createElement('dialog')
    dialog.setAttribute('open', '')
    document.body.appendChild(dialog)
    await waitFor(() => expect(cmds('hide')).toHaveLength(1))
    expect(await screen.findByText(/picture is hidden while a menu is open/)).toBeInTheDocument()
    dialog.remove()
    await waitFor(() => expect(cmds('popup').length).toBeGreaterThan(0))
  })

  it('opens every menu upwards over the picture, and the picture steps aside while one is open', async () => {
    mount()
    await ready()
    await waitFor(() => expect(calls.some((c) => c.command === 'native_rect')).toBe(true))
    const hidden = cmds('hide').length
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const menu = await screen.findByRole('menu', { name: 'Settings' })
    expect(menu.className).toContain('mo-drop-up')                           // above the bar, not below it
    expect(menu.className).not.toContain('mo-drop-down')
    await waitFor(() => expect(cmds('hide')).toHaveLength(hidden + 1))
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(cmds('popup').length).toBeGreaterThan(1))      // closed: the picture is back
  })

  it('shows itself over the screen when the whole page is in full screen (Play in SFW), without hiding its own picture', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    mount()
    await ready()
    await waitFor(() => expect(calls.some((c) => c.command === 'native_rect')).toBe(true))
    const hidden = cmds('hide').length
    act(() => enterFullscreen())
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
    const pane = screen.getByTestId('native-pane')
    expect(pane.getAttribute('popover')).toBe('manual')                                  // in the top layer, over whatever the page is doing
    expect(pane.style.position).toBe('fixed')
    expect(await screen.findByRole('button', { name: 'Back' })).toBeInTheDocument()      // the full screen bar: the way back (no name of the video)
    await new Promise((r) => setTimeout(r, 150))
    expect(cmds('hide')).toHaveLength(hidden)                                            // its own top-layer box is not "something over the picture"
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
    act(() => { leaveFullscreen(); document.dispatchEvent(new Event('fullscreenchange')) })
    expect(screen.getByTestId('native-pane').getAttribute('popover')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
  })

  it('hides the picture when the stage is scrolled partly out of view', async () => {
    mount()
    await ready()
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(() => ({ ...BOX, top: -40, bottom: 320 }) as DOMRect)
    fireEvent.scroll(window)
    await waitFor(() => expect(cmds('hide')).toHaveLength(1))
  })

  it('says why it cannot play and offers the browser player', async () => {
    overrides.native_available = () => ({ available: false, reason: 'The native engine is not installed.' })
    const onBrowser = vi.fn()
    mount({ onBrowser })
    expect(await screen.findByText('The native engine is not installed.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use the browser player' }))
    expect(onBrowser).toHaveBeenCalledTimes(1)
  })

  it('reports the end and goes on to the next video when autonext is on', async () => {
    const onEnd = vi.fn(); const onNext = vi.fn()
    state = { ...state, ended: true, playing: false }
    mount({ onEnd, onNext, autoNext: true })
    await waitFor(() => expect(onEnd).toHaveBeenCalledTimes(1))
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it('closes the engine when the pane goes away', async () => {
    const { unmount } = mount()
    await ready()
    unmount()
    await waitFor(() => expect(cmds('close').length).toBeGreaterThan(0))
  })
})

describe('NativePane after a jump', () => {
  it('stops for a moment when the picture crawls, then plays on', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
    mount({ seekRequest: { t: 60, n: 1 } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    expect(cmds('play')).toHaveLength(1)
    expect(cmds('seek').some((c) => c.args?.value === 60)).toBe(true)
    let pos = 60
    overrides.native_state = () => { pos += 0.05; return { ...state, position: pos } }          // the position crawls at 0.2x real time
    for (let i = 0; i < 12; i++) await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(cmds('pause')).toHaveLength(1)
    expect(screen.getByText('Buffering after the jump…')).toBeInTheDocument()
    for (let i = 0; i < 20 && cmds('play').length < 2; i++) await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(cmds('play')).toHaveLength(2)                                                         // the refill ended: it carried on
    expect(cmds('pause')).toHaveLength(1)
  })

  it('does not interfere when the picture runs at real speed', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
    mount({ seekRequest: { t: 60, n: 1 } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    let pos = 60
    overrides.native_state = () => { pos += 0.25; return { ...state, position: pos } }
    for (let i = 0; i < 40; i++) await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(cmds('pause')).toHaveLength(0)
  })

  it('a pause the person chose during the refill is not undone', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
    mount({ seekRequest: { t: 60, n: 1 } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    let pos = 60
    overrides.native_state = () => { pos += 0.05; return { ...state, position: pos } }
    for (let i = 0; i < 12 && cmds('pause').length < 1; i++) await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(cmds('pause')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))                              // the person presses Pause during the refill
    for (let i = 0; i < 20; i++) await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(cmds('play')).toHaveLength(1)                                                         // only the first start: it did not resume by itself
  })
})

describe('NativePane, an add-on stream and full screen', () => {
  it('opens the address the server gives for an add-on stream, not a library video', async () => {
    ;(fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => String(url).includes('/api/screen/stream-link/') ? new Response(JSON.stringify({ url: 'https://cdn.example/film.mkv' }), { status: 200 }) : new Response('{}', { status: 404 }))
    render(<NativePane linkPath="/api/screen/stream-link/tok1" addon={{ kind: 'movie', ident: 'tt0133093' }} durationHint={120} />)
    await ready()
    expect(calls.find((c) => c.command === 'native_open')!.args).toMatchObject({ url: 'https://cdn.example/film.mkv' })
  })

  it('has a back arrow in full screen, and no name of the video', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    const onClose = vi.fn()
    mount({ onClose })
    await ready()
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()   // on the page the page's own back arrow is used
    act(() => enterFullscreen())
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
    expect(await screen.findByRole('button', { name: 'Back' })).toBeInTheDocument()
    expect(screen.queryByText('The Matrix')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Leave full screen' })).toBeNull()   // the bottom bar has the full screen button
    fireEvent.click(await screen.findByRole('button', { name: 'Back' }))
    expect(onClose).toHaveBeenCalledTimes(1)                                   // the arrow leaves full screen and goes back
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
  })

  it('full screen is the whole page, from its own button too', async () => {
    const request = vi.fn(async () => undefined)
    document.documentElement.requestFullscreen = request as never
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Full screen' }))
    expect(request).toHaveBeenCalledTimes(1)
    act(() => leaveFullscreen())
  })

  // ---- the see-through arrangement (dx.bi's): in full screen the video is a window UNDER the app window and the page is drawn over it ----------------------------
  const fullScreenNow = () => {
    act(() => enterFullscreen())
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
  }
  const fullScreenOver = () => {
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
    act(() => { leaveFullscreen(); document.dispatchEvent(new Event('fullscreenchange')) })
  }
  const answerUnder = () => { overrides.native_open = () => ({ ...OPENED, layer: 'under' }); state = { ...state, layer: 'under' } }

  it('asks for the under layer when the page is already in full screen, and draws the controls and the box over the picture', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    answerUnder()
    fullScreenNow()
    mount()
    await ready()
    expect(calls.find((c) => c.command === 'native_open')!.args).toMatchObject({ layer: 'under' })
    const pane = screen.getByTestId('native-pane')
    expect(pane.hasAttribute('data-ax-under-pane')).toBe(true)
    expect(document.documentElement.classList.contains('ax-under')).toBe(true)          // the page and the window around the pane are see-through
    expect(pane.className).toContain('bg-transparent')
    const controls = screen.getByRole('button', { name: 'Pause' }).closest('[data-ov]') as HTMLElement
    expect(controls.className).toContain('absolute')                                    // the controls are on the picture, not under it
    expect(controls.className).toContain('bottom-0')
    await new Promise((r) => setTimeout(r, 150))
    expect(cmds('hide')).toHaveLength(0)                                                // nothing steps aside: the video window is under the page
    expect(screen.queryByText(/The picture is hidden/)).toBeNull()
    expect(calls.some((c) => c.command === 'native_rect')).toBe(true)                   // it still follows the stage
    fullScreenOver()
    expect(document.documentElement.classList.contains('ax-under')).toBe(false)         // out of full screen the page is solid again
  })

  it('a menu opening over the picture does not take the picture away in the under layer', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    answerUnder()
    fullScreenNow()
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    await screen.findByRole('menu', { name: 'Settings' })
    await new Promise((r) => setTimeout(r, 200))
    expect(cmds('hide')).toHaveLength(0)
    fullScreenOver()
  })

  it('moves the video between the window above the page and the window under it as full screen starts and ends', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    mount()
    await ready()
    expect(cmds('under')).toHaveLength(0)
    fullScreenNow()
    await waitFor(() => expect(cmds('under')).toHaveLength(1))
    fullScreenOver()
    await waitFor(() => expect(cmds('popup').length).toBeGreaterThan(0))
    expect(cmds('under')).toHaveLength(1)
  })

  it('stays with the window above the page when the engine cannot do the under layer (it answers popup)', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    fullScreenNow()
    mount()                                                                              // asked for under, the default mock answers popup
    await ready()
    expect(calls.find((c) => c.command === 'native_open')!.args).toMatchObject({ layer: 'under' })
    expect(document.documentElement.classList.contains('ax-under')).toBe(false)
    expect(screen.getByTestId('native-pane').hasAttribute('data-ax-under-pane')).toBe(false)
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument()            // the full screen layout with the controls around the picture, as before
    fullScreenOver()
  })

  it('goes back to the window above the page, for good, when the screen over the picture stays flat while it plays', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    answerUnder()
    state = { ...state, position: 30, screenDistinct: 1, screenLumaRange: 0, playing: true }
    overrides.native_state = () => ({ ...state })                                   // the real shell answers a new object every time
    fullScreenNow()
    mount()
    await ready()
    await waitFor(() => expect(cmds('popup').length).toBeGreaterThan(0), { timeout: 8000 })   // about four seconds of flat screen
    fullScreenOver()
  }, 12_000)
})
