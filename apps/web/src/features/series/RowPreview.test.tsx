import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PosterCard } from './PosterCard'
import { useRowPreview, type Previewable } from './RowPreview'
import { withRouter } from '@/test/withRouter'

const item: Previewable = { imdb: 'tt0133093', kind: 'movie', title: 'The Matrix', year: 1999, poster: '', background: '', rating: '8.7', overview: 'A hacker learns the truth.', genres: ['Action', 'Sci-Fi'], library: null }

function Row() {
  const p = useRowPreview()
  return <><PosterCard image="" title={item.title} onHover={p.hover(item)} />{p.node}</>
}

class FakePointerEvent extends MouseEvent {   // jsdom has no PointerEvent
  pointerType: string
  constructor(type: string, init: MouseEventInit & { pointerType?: string } = {}) { super(type, init); this.pointerType = init.pointerType ?? 'mouse' }
}
beforeEach(() => { vi.stubGlobal('PointerEvent', FakePointerEvent); vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('hover: hover'), addEventListener() {}, removeEventListener() {} })) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

const rect = (l: number, t: number, r: number, b: number) => ({ left: l, top: t, right: r, bottom: b, width: r - l, height: b - t, x: l, y: t, toJSON() {} }) as DOMRect
const at = (x: number, y: number) => fireEvent(document, Object.assign(new MouseEvent('pointermove', { clientX: x, clientY: y, bubbles: true }), { pointerType: 'mouse' }))

describe('a row card preview', () => {
  it('unfolds after the pointer rests on the card, shows what the list knows, and closes after the pointer has left it', async () => {
    withRouter(<Row />)
    const card = await screen.findByRole('button', { name: 'Details of The Matrix' })
    vi.spyOn(card, 'getBoundingClientRect').mockReturnValue(rect(100, 100, 270, 350))
    vi.useFakeTimers()
    fireEvent.pointerEnter(card, { pointerType: 'mouse' })
    expect(screen.queryByRole('dialog')).toBeNull()                                      // not at once: passing over a row opens nothing
    act(() => { vi.advanceTimersByTime(500) })
    const box = screen.getByRole('dialog', { name: 'Preview of The Matrix' })
    expect(box).not.toHaveTextContent('A hacker learns the truth.')   // like dx.bi's panel: buttons, rating, year, length and genres, no story
    expect(box).toHaveTextContent('Action · Sci-Fi')
    expect(box).toHaveTextContent('8.7')
    at(150, 200)                                                                         // still on the card
    act(() => { vi.advanceTimersByTime(400) })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    at(900, 800)                                                                         // far away
    act(() => { vi.advanceTimersByTime(500) })                                            // the pause, then the short fade out
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('stays while the pointer is on the preview itself', async () => {
    withRouter(<Row />)
    const card = await screen.findByRole('button', { name: 'Details of The Matrix' })
    vi.spyOn(card, 'getBoundingClientRect').mockReturnValue(rect(100, 100, 270, 350))
    vi.useFakeTimers()
    fireEvent.pointerEnter(card, { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(500) })
    vi.spyOn(screen.getByRole('dialog'), 'getBoundingClientRect').mockReturnValue(rect(40, 84, 380, 420))
    at(60, 400)                                                                          // off the card, on the preview
    act(() => { vi.advanceTimersByTime(400) })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('puts a row back when the pointer goes from it straight to a card of another row', async () => {
    const other: Previewable = { ...item, imdb: 'tt0234215', title: 'Reloaded' }
    function TwoRows() {
      const a = useRowPreview(), b = useRowPreview()
      return <>
        <div data-testid="row-a" className="flex"><PosterCard image="" title={item.title} onHover={a.hover(item)} /><PosterCard image="" title="Neighbour A" /></div>{a.node}
        <div data-testid="row-b" className="flex"><PosterCard image="" title={other.title} onHover={b.hover(other)} /></div>{b.node}
      </>
    }
    withRouter(<TwoRows />)
    const first = await screen.findByRole('button', { name: 'Details of The Matrix' }), second = await screen.findByRole('button', { name: 'Details of Reloaded' })
    vi.spyOn(first, 'getBoundingClientRect').mockReturnValue(rect(100, 100, 270, 350))
    vi.spyOn(second, 'getBoundingClientRect').mockReturnValue(rect(100, 500, 270, 750))
    vi.useFakeTimers()
    fireEvent.pointerEnter(first, { pointerType: 'mouse' }); act(() => { vi.advanceTimersByTime(500) })
    expect(screen.getByRole('dialog', { name: 'Preview of The Matrix' })).toBeInTheDocument()
    const neighbour = screen.getByTestId('row-a').children[1] as HTMLElement
    act(() => { vi.advanceTimersByTime(50) })                                  // the titles glide aside
    expect(neighbour.style.transform).not.toBe('')                           // (the preview has pushed it aside)
    fireEvent.pointerEnter(second, { pointerType: 'mouse' }); act(() => { vi.advanceTimersByTime(600) })    // the next row opens while this one is still open (a quick move)
    expect(screen.getAllByRole('dialog').length).toBe(2)
    at(900, 900); act(() => { vi.advanceTimersByTime(1500) })                                                              // and away
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(neighbour.style.transform).toBe('')                                    // the first row went back, whichever row opened last
    expect(first.parentElement!.style.transform).toBe('')
    expect(first.style.opacity).toBe('')
  })
})
