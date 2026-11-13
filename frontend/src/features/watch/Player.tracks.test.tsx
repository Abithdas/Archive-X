import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Player } from './Player'

const tracks = {
  audio: [{ index: 1, ordinal: 0, label: 'English', language: 'eng', default: true }, { index: 2, ordinal: 1, label: 'Spanish', language: 'spa', default: false }],
  subtitles: [{ index: 3, ordinal: 0, label: 'English', language: 'eng', text: true, forced: false, default: false }],
  captions: { available: true, engine: 'stub', install: '' },
}
const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200 }))

beforeEach(() => { localStorage.clear() })
afterEach(() => vi.unstubAllGlobals())

describe('Player audio / subtitle menu', () => {
  it('lists audio and subtitle choices and shows the chosen subtitle at the playback time', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/api/play/tracks/')) return json(tracks)
      if (url.includes('/api/play/subtitle/')) return Promise.resolve(new Response('WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello there\n', { status: 200 }))
      return Promise.resolve(new Response('', { status: 404 }))
    }))
    const { container } = render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Audio and subtitles' }))
    expect(screen.getByRole('menuitemradio', { name: /Spanish/ })).toBeInTheDocument()
    expect(screen.getByText(/Live captions → English/)).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('menuitemradio', { name: 'English' })[1]!)   // the subtitle column
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'currentTime', { value: 2, configurable: true, writable: true })
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes('/subtitle/-1/1/3.vtt'))).toBe(true))
    await act(async () => { fireEvent.timeUpdate(v) })
    await waitFor(() => expect(screen.getByTestId('caption')).toHaveTextContent('Hello there'))
  })

  it('has no audio/subtitle menu when the server reports nothing to choose and no engine', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ audio: [{ index: 1, ordinal: 0, label: 'a', language: '', default: true }], subtitles: [], captions: { available: false, engine: '', install: 'x' } })))
    render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByRole('button', { name: 'Audio and subtitles' })).toBeNull()
  })

  it('switches to a second audio track by asking the server to prepare it and muting the video', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/api/play/tracks/')) return json(tracks)
      if (url.includes('/audio-track/-1/1/2/status')) return json({ ready: true, url: '/api/play/audio-track/-1/1/2.m4a' })
      return Promise.resolve(new Response('', { status: 404 }))
    }))
    const { container } = render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Audio and subtitles' }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Spanish/ }))
    await waitFor(() => expect(container.querySelector('audio')?.getAttribute('src')).toBe('/api/play/audio-track/-1/1/2.m4a'))
    expect(container.querySelector('video')!.muted).toBe(true)
  })
})

describe('Player settings and tap skipping', () => {
  it('offers live captions in the settings menu', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => (url.includes('/api/play/tracks/') ? json(tracks) : Promise.resolve(new Response('', { status: 404 })))))
    render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    await screen.findByRole('button', { name: 'Audio and subtitles' })
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('menuitemradio', { name: 'English (translated)' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Original language' }))
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('menuitemradio', { name: 'Original language' })).toHaveAttribute('aria-checked', 'true')
  })
  it('keeps adding 10 s per tap on the right side, and shows the running total', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    Object.defineProperty(v, 'duration', { value: 600, configurable: true }); v.currentTime = 100
    v.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 500, right: 1000, bottom: 500, x: 0, y: 0, toJSON() {} })
    for (let i = 0; i < 3; i++) fireEvent.click(v, { clientX: 900 })
    expect(v.currentTime).toBe(120)   // first tap arms, the next two skip
    expect(screen.getByTestId('skip-indicator')).toHaveTextContent('20 seconds')
  })
})

describe('live caption model', () => {
  it('starts the model download when live captions are chosen and shows the captions once ready', async () => {
    let calls = 0
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/api/play/tracks/')) return json(tracks)
      if (url.includes('/api/captions/status')) { calls++; return json({ model: calls > 1 ? 'ready' : 'preparing' }) }
      if (url.includes('/api/play/captions/')) return json({ cues: [{ start: 0, end: 5, text: 'Hello from speech' }] })
      return Promise.resolve(new Response('', { status: 404 }))
    }))
    const { container } = render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Audio and subtitles' }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Live captions → English/ }))
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes('/api/captions/status?prepare=1'))).toBe(true))
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes('/api/play/captions/-1/1?start=0'))).toBe(true), { timeout: 4000 })
    await act(async () => { fireEvent.timeUpdate(container.querySelector('video')!) })
    await waitFor(() => expect(screen.getByTestId('caption')).toHaveTextContent('Hello from speech'))
  })
})

describe('caption look and status', () => {
  it('Settings lets you change caption size and background, and remembers it', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => (url.includes('/api/play/tracks/') ? json(tracks) : Promise.resolve(new Response('', { status: 404 })))))
    render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    await screen.findByRole('button', { name: 'Audio and subtitles' })
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Larger captions' }))
    expect(screen.getByLabelText('Caption size')).toHaveTextContent('115%')
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Solid box' }))
    expect(JSON.parse(localStorage.getItem('archive-x-caption-style') ?? localStorage.getItem('archive-x:caption-style') ?? '{}')).toMatchObject({ size: 1.15, bg: 'solid' })
  })
  it('says in the picture what live captions are doing instead of leaving it to guesswork', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/api/play/tracks/')) return json(tracks)
      if (url.includes('/api/captions/status')) return json({ model: 'preparing' })
      return Promise.resolve(new Response('', { status: 404 }))
    }))
    render(<Player src="/stream/-1/1" media={{ cid: '-1', mid: 1 }} durationHint={60} onOpenVlc={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Audio and subtitles' }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Live captions → English/ }))
    expect(await screen.findByText(/getting the speech model ready/)).toBeInTheDocument()
  })
})
