import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HoverTrailer } from './HoverTrailer'
import { withRouter } from '@/test/withRouter'
import { useRowPreview, type Previewable } from './RowPreview'
import { PosterCard } from './PosterCard'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
const say = (frame: HTMLIFrameElement, info: object) => act(() => { window.dispatchEvent(new MessageEvent('message', { source: frame.contentWindow, data: JSON.stringify({ event: 'infoDelivery', info }) })) })

describe('the trailer in an expanded card', () => {
  it('loads muted and hidden, shows itself only once YouTube really plays, and the speaker button toggles the sound', () => {
    vi.useFakeTimers()
    const playing = vi.fn()
    render(<HoverTrailer id="dQw4w9WgXcQ" title="Film" onPlaying={playing} />)
    const frame = document.querySelector('iframe')!
    expect(frame.src).toContain('youtube-nocookie.com/embed/dQw4w9WgXcQ')
    expect(frame.src).toContain('mute=1'); expect(frame.src).toContain('autoplay=1'); expect(frame.src).toContain('controls=0')
    expect(screen.getByRole('button', { name: 'Unmute trailer' })).toHaveClass('opacity-0')   // nothing to press before it plays
    const sent: string[] = []
    vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation(((m: string) => { sent.push(String(m)) }) as never)
    say(frame, { playerState: 1, currentTime: 0.2 })
    expect(playing).not.toHaveBeenCalledWith(true)                                           // YouTube's own controls are still fading
    act(() => { vi.advanceTimersByTime(2900) })
    expect(playing).toHaveBeenCalledWith(true)
    expect(document.querySelector('[data-hover-trailer]')).toHaveClass('opacity-100')
    fireEvent.click(screen.getByRole('button', { name: 'Unmute trailer' }))
    expect(sent.some((m) => m.includes('"unMute"'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Mute trailer' }))
    expect(sent.some((m) => m.includes('"mute"'))).toBe(true)
  })

  it('opens nothing for an id that is not a YouTube id, and drops out when YouTube reports an error', () => {
    const { container, rerender } = render(<HoverTrailer id="../x" title="Film" onPlaying={() => undefined} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<HoverTrailer id="dQw4w9WgXcQ" title="Film" onPlaying={() => undefined} />)
    const frame = document.querySelector('iframe')!
    act(() => { window.dispatchEvent(new MessageEvent('message', { source: frame.contentWindow, data: JSON.stringify({ event: 'onError', info: 150 }) })) })
    expect(container).toBeEmptyDOMElement()
  })

  it('the full screen button opens the trailer on its own layer, with sound, from where it had got to', () => {
    render(<HoverTrailer id="dQw4w9WgXcQ" title="Film" onPlaying={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Watch trailer full screen' }))
    const layer = document.querySelector('[data-trailer-theatre]')!
    expect(layer.querySelector('iframe')!.src).toContain('mute=0')
    fireEvent.keyDown(window, { key: 'Escape' })
    layer.remove()
  })
})

const item: Previewable = { imdb: 'tt0133093', kind: 'movie', title: 'The Matrix', year: 1999, poster: '', background: '', rating: '8.7', overview: '', genres: ['Action'], library: null }
function Row() { const p = useRowPreview(); return <><PosterCard image="" title={item.title} onHover={p.hover(item)} />{p.node}</> }
class Ptr extends MouseEvent { pointerType = 'mouse' }

describe('which cards get a trailer', () => {
  const run = async (mode: string, hover_trailers = true) => {
    vi.stubGlobal('PointerEvent', Ptr)
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('hover: hover'), addEventListener() {}, removeEventListener() {} }))
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const body = String(u).includes('/api/content-mode') ? { mode } : String(u).includes('/api/settings/experience') ? { hover_trailers } : String(u).includes('/api/screen/discover/') ? { runtime: '120 min', trailer: 'dQw4w9WgXcQ' } : {}
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    withRouter(<Row />)
    const card = await screen.findByRole('button', { name: 'Details of The Matrix' })
    vi.spyOn(card, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 100, right: 270, bottom: 350, width: 170, height: 250, x: 100, y: 100, toJSON() {} } as DOMRect)
    fireEvent.pointerEnter(card, { pointerType: 'mouse' })
    await screen.findByRole('dialog', { name: 'Preview of The Matrix' }, { timeout: 3000 })
    await new Promise((r) => setTimeout(r, 400))
  }
  it('SFW with the setting on: the card loads its trailer', async () => { await run('sfw'); expect(document.querySelector('[data-hover-trailer] iframe')).not.toBeNull() })
  it('NSFW: never', async () => { await run('nsfw'); expect(document.querySelector('[data-hover-trailer]')).toBeNull() })
  it('SFW with the setting off: never', async () => { await run('sfw', false); expect(document.querySelector('[data-hover-trailer]')).toBeNull() })
})
