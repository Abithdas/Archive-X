import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { NativePlayerPage } from './NativePlayerPage'

type Call = { command: string; args: Record<string, unknown> | undefined }
let calls: Call[]
let state: Record<string, unknown>
let overrides: Record<string, () => unknown>

const SIGNED = 'http://127.0.0.1:18570/video/5?c=-1001&t=abc'
const OPENED = { duration: 120, width: 1920, height: 1080, decoder: 'avcodec', layer: 'front' }

beforeEach(() => {
  calls = []
  overrides = {}
  state = { position: 12, duration: 120, playing: true, ended: false, buffering: false, width: 1920, height: 1080, error: 0, opened: true, message: '', windowVisible: true, windowWidth: 1280, windowHeight: 720, layer: 'front', framesDrawn: 300, framesDropped: 0, screenDistinct: 40, screenLumaRange: 180 }
  document.body.innerHTML = '<div id="root"></div>'
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (String(url).includes('/api/play/device-link/') ? new Response(JSON.stringify({ url: SIGNED }), { status: 200 }) : new Response('{}', { status: 404 }))))
  ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
    invoke: vi.fn(async (command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args })
      if (overrides[command]) return overrides[command]!()
      switch (command) {
        case 'native_available': return { available: true, reason: '' }
        case 'native_open': return OPENED
        case 'native_state': return state
        case 'native_diagnostics': return 'layer: front'
        case 'native_tracks': return [{ name: 'English', selected: true }, { name: 'Spanish', selected: false }]
        default: return undefined
      }
    }),
  }
})
afterEach(() => { vi.unstubAllGlobals(); delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ })

const sent = (command: string) => calls.filter((c) => c.command === command)
const mount = () => withRouter(<NativePlayerPage cid="-1001" mid={5} />)
const ready = async () => { await waitFor(() => expect(sent('native_command').some((c) => c.args?.command === 'play')).toBe(true)); await screen.findByRole('button', { name: 'Pause' }) }

describe('NativePlayerPage', () => {
  it('opens the video from the signed address, starts it and names the decoder', async () => {
    mount()
    await ready()
    const open = sent('native_open')[0]!
    expect(open.args).toMatchObject({ url: SIGNED })
    expect(typeof open.args!.w).toBe('number')
    expect(await screen.findByText(/decoder avcodec/)).toBeInTheDocument()
    expect(screen.getByText(/video in front/)).toBeInTheDocument()
    expect(await screen.findByText(/video window shown 1280×720/)).toBeInTheDocument()
    expect(screen.getByText('0:12 / 2:00')).toBeInTheDocument()
  })

  it('hides the rest of the app and makes the page see-through while it is open, and puts it back afterwards', async () => {
    const root = document.getElementById('root')!
    const { unmount } = mount()
    await ready()
    expect(root.style.visibility).toBe('hidden')
    expect(document.body.style.background).toBe('transparent')
    unmount()
    expect(root.style.visibility).toBe('')
    expect(document.body.style.background).toBe('')
    await waitFor(() => expect(sent('native_command').some((c) => c.args?.command === 'close')).toBe(true))
  })

  it('says why when the engine is not there, and opens nothing', async () => {
    overrides.native_available = () => ({ available: false, reason: 'the native player engine is not installed (axplayer.dll is missing)' })
    mount()
    expect((await screen.findAllByText(/axplayer\.dll is missing/)).length).toBeGreaterThan(0)
    expect(sent('native_open')).toHaveLength(0)
  })

  it('shows a failure to open with its reason', async () => {
    overrides.native_open = () => { throw 'opening the address failed (0x80040265)' }
    mount()
    expect((await screen.findAllByText(/Could not play: opening the address failed/)).length).toBeGreaterThan(0)
  })

  it('sends pause, mute, speed, volume and audio-track choices to the player', async () => {
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Speed' }), { target: { value: '1.5' } })
    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '40' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Audio track' }), { target: { value: '1' } })
    const cmds = sent('native_command').map((c) => `${String(c.args?.command)}:${String(c.args?.value)}`)
    expect(cmds).toEqual(expect.arrayContaining(['pause:undefined', 'mute:1', 'rate:1.5', 'volume:40', 'audio:1']))
  })

  it('seeks when the seek bar is released, not while it is dragged', async () => {
    mount()
    await ready()
    const bar = screen.getByRole('slider', { name: 'Seek' })
    fireEvent.change(bar, { target: { value: '60' } })
    expect(sent('native_command').some((c) => c.args?.command === 'seek')).toBe(false)
    fireEvent.pointerUp(bar)
    expect(sent('native_command').find((c) => c.args?.command === 'seek')?.args).toMatchObject({ command: 'seek', value: 60 })
  })

  it('shows the place that was jumped to, and says so, until the player gets there', async () => {
    mount()
    await ready()
    const bar = screen.getByRole('slider', { name: 'Seek' })
    fireEvent.change(bar, { target: { value: '90' } })
    fireEvent.pointerUp(bar)
    expect(screen.getByText(/1:30 \/ 2:00/)).toBeInTheDocument()
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('Jumping…')
    state = { ...state, position: 91 }
    await act(async () => { await new Promise((r) => setTimeout(r, 400)) })
    await waitFor(() => expect(screen.queryByText('Jumping…')).toBeNull())
    expect(screen.getByText(/1:31 \/ 2:00/)).toBeInTheDocument()
  })

  it('follows the player: a paused video offers Play', async () => {
    mount()
    await ready()
    state = { ...state, playing: false, position: 30 }
    await act(async () => { await new Promise((r) => setTimeout(r, 400)) })
    expect(await screen.findByRole('button', { name: 'Play' })).toBeInTheDocument()
    expect(screen.getByText('0:30 / 2:00')).toBeInTheDocument()
  })

  it('lets the viewer step the video through front, own window and behind the page, while it plays', async () => {
    mount()
    await ready()
    const layer = screen.getByRole('button', { name: 'Video layer' })
    expect(layer).toHaveTextContent('Video: in front')
    fireEvent.click(layer)
    expect(layer).toHaveTextContent('Video: own window')
    expect(sent('native_command').some((c) => c.args?.command === 'popup')).toBe(true)
    fireEvent.click(layer)
    expect(layer).toHaveTextContent('Video: behind page')
    expect(sent('native_command').some((c) => c.args?.command === 'back')).toBe(true)
    expect(screen.getByText(/video behind the page/)).toBeInTheDocument()
    fireEvent.click(layer)
    expect(sent('native_command').filter((c) => c.args?.command === 'front').length).toBeGreaterThan(0)
  })

  it('says when nothing but a flat colour is seen where the video is', async () => {
    state = { ...state, screenDistinct: 1, screenLumaRange: 0 }
    mount()
    await ready()
    expect(await screen.findByText(/FLAT \(no picture seen\)/)).toBeInTheDocument()
  })

  it('says when the video window is hidden, so a missing picture can be explained', async () => {
    state = { ...state, windowVisible: false, windowWidth: 0, windowHeight: 0 }
    mount()
    await ready()
    expect(await screen.findByText(/video window HIDDEN 0×0/)).toBeInTheDocument()
  })
})

describe('leaving the native player', () => {
  it('hides the picture at once when Back is pressed, then goes back', async () => {
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: /Back/ }))
    expect(sent('native_command').some((c) => c.args?.command === 'hide')).toBe(true)
  })
})
