import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { WatchPage } from './WatchPage'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), loading: vi.fn(), dismiss: vi.fn() }), Toaster: () => null }))

const ITEM = { id: 5, _cid: '-1001', media_id: 9, name: 'A video', filename: 'a.mkv', size: 1e8, duration: 120, width: 1920, height: 1080, resolution_category: '1080p', codec: 'hevc', thumbnail_id: 5, favorite: 0, upload_date: '2025-01-01', channel_title: 'Chan', performers: [], studios: [], tags: [], studio: '' }
let nativeCalls: string[]
let mode = 'fallback'

beforeEach(() => {
  nativeCalls = []
  document.body.innerHTML = ''
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url)
    const json = (o: unknown, code = 200) => new Response(JSON.stringify(o), { status: code })
    if (u.startsWith('/api/library?')) return json({ total: 1, page: 0, per: 1, items: [ITEM] })
    if (u === '/api/settings/experience') return json({ autoplay: true, native_player: mode, resume_prompt: false })
    if (u.includes('/api/play/device-link/')) return json({ url: 'http://127.0.0.1:1/video/5' })
    if (u.includes('/api/play/info/')) return json({ direct_likely: false, name: 'a.mkv', vcodec: 'hevc', acodec: 'aac' })
    if (u.includes('/api/play/tracks/')) return json({ audio: [], subtitles: [], captions: { available: false, engine: '', install: '' } })
    return json({}, 404)
  }))
  ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
    invoke: vi.fn(async (command: string, args?: Record<string, unknown>) => {
      nativeCalls.push(command === 'native_command' ? `cmd:${String(args?.command)}` : command)
      if (command === 'native_available') return { available: true, reason: '' }
      if (command === 'native_open') return { duration: 120, width: 1920, height: 1080, decoder: 'avcodec', layer: 'popup' }
      if (command === 'native_state') return { position: 1, duration: 120, playing: true, ended: false, buffering: false, width: 1920, height: 1080, error: 0, opened: true, message: '', windowVisible: true, windowWidth: 1, windowHeight: 1, layer: 'popup', framesDrawn: 1, framesDropped: 0, screenDistinct: 5, screenLumaRange: 9 }
      if (command === 'native_tracks') return []
      return undefined
    }),
  }
  window.history.replaceState({}, '', '/')
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__; window.history.replaceState({}, '', '/') })

const mount = () => withRouter(<WatchPage cid="-1001" mid={5} />)
const video = () => document.querySelector('video')

describe('which engine plays the video', () => {
  it('"always": the native player is in the page from the start, with the page around it, and the browser player is not used', async () => {
    mode = 'always'
    mount()
    expect(await screen.findByTestId('native-pane')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'A video' })).toBeInTheDocument()               // the same page: title, details, up next
    expect(video()).toBeNull()
    expect(nativeCalls).toContain('native_open')
  })

  it('"fallback" (the default): the browser plays first, and the native player takes over only when it cannot', async () => {
    mode = 'fallback'
    mount()
    await waitFor(() => expect(video()).not.toBeNull())
    expect(screen.queryByTestId('native-pane')).toBeNull()
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })                    // the engine check finishes
    fireEvent.error(video()!)                                                                   // the browser cannot decode it
    expect(await screen.findByTestId('native-pane')).toBeInTheDocument()
    expect(video()).toBeNull()
  })

  it('"off": nothing switches by itself, but the button on the page does, and switches back', async () => {
    mode = 'off'
    mount()
    await waitFor(() => expect(video()).not.toBeNull())
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
    fireEvent.error(video()!)
    expect(screen.queryByTestId('native-pane')).toBeNull()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Play with the native player' }))[0]!)   // the failure screen has one too, and so does Actions
    expect(await screen.findByTestId('native-pane')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use the browser player' }))
    await waitFor(() => expect(video()).not.toBeNull())
    expect(screen.queryByTestId('native-pane')).toBeNull()
  })

  it('"always" still lets you use the browser player for one video, and does not switch back by itself', async () => {
    mode = 'always'
    mount()
    await screen.findByTestId('native-pane')
    fireEvent.click(screen.getByRole('button', { name: 'Use the browser player' }))
    await waitFor(() => expect(video()).not.toBeNull())
    await act(async () => { await new Promise((r) => setTimeout(r, 100)) })
    expect(screen.queryByTestId('native-pane')).toBeNull()
  })

  it('an old native-player link (?engine=native) opens the native player whatever the setting', async () => {
    mode = 'off'
    window.history.replaceState({}, '', '/?engine=native')
    mount()
    expect(await screen.findByTestId('native-pane')).toBeInTheDocument()
  })

  it('in a browser (no desktop app) the native player is never offered', async () => {
    mode = 'always'
    delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
    mount()
    await waitFor(() => expect(video()).not.toBeNull())
    expect(screen.queryByTestId('native-pane')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Play with the native player' })).toBeNull()
  })

  it('"fallback" does not loop: after choosing the browser player and a failure, it stays in the browser player', async () => {
    mode = 'fallback'
    mount()
    await waitFor(() => expect(video()).not.toBeNull())
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
    fireEvent.error(video()!)
    await screen.findByTestId('native-pane')
    fireEvent.click(screen.getByRole('button', { name: 'Use the browser player' }))
    await waitFor(() => expect(video()).not.toBeNull())
    fireEvent.error(video()!)
    await act(async () => { await new Promise((r) => setTimeout(r, 100)) })
    expect(screen.queryByTestId('native-pane')).toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()                                       // the failure screen stays, with its own buttons
  })
})
