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
