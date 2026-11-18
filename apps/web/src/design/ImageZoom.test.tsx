import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ZoomImage } from './ImageZoom'

// jsdom never loads an image, so the viewer's loader is told they all load at once.
vi.mock('@/features/photos/imageCache', () => ({
  loadImage: () => Promise.resolve({ naturalWidth: 800, naturalHeight: 600 }),
  firstLoaded: (urls: string[]) => Promise.resolve({ url: urls[0], img: { naturalWidth: 800, naturalHeight: 600 } }),
}))
HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }

afterEach(() => { vi.restoreAllMocks(); delete (Element.prototype as { animate?: unknown }).animate })

function fakeAnimate() {
  const runs: Array<{ el: Element; keyframes: Keyframe[]; options: KeyframeAnimationOptions; finish: () => void }> = []
  ;(Element.prototype as unknown as { animate: unknown }).animate = function (this: Element, keyframes: Keyframe[], options: KeyframeAnimationOptions) {
    const a = { onfinish: null as null | (() => void), oncancel: null as null | (() => void) }
    runs.push({ el: this, keyframes, options, finish: () => a.onfinish?.() })
    return a
  }
  return runs
}
const box = (left: number, top: number, width: number, height: number) => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() {} })
const viewer = () => screen.findByRole('dialog', { name: 'Photo viewer' })

describe('ZoomImage', () => {
  it('is an image you can click or press Enter on, named for what it will do', () => {
    render(<ZoomImage src="/t.jpg" alt="Snapshot 1" className="thumb" />)
    const img = screen.getByRole('button', { name: 'Enlarge: Snapshot 1' })
    expect(img).toHaveClass('thumb'); expect(img).toHaveAttribute('aria-expanded', 'false'); expect(img.tabIndex).toBe(0)
  })
  it('opens the photo viewer on the larger picture, and closing it puts focus back on the thumbnail', async () => {
    render(<ZoomImage src="/small.jpg" fullSrc="/big.jpg" alt="Snapshot 1" />)
    const img = screen.getByRole('button', { name: 'Enlarge: Snapshot 1' })
    fireEvent.click(img)
    const dlg = await viewer()
    expect(img).toHaveAttribute('aria-expanded', 'true')
    expect(within(dlg).getByRole('toolbar', { name: 'Photo viewer controls' })).toBeInTheDocument()   // zoom, rotate, full screen: the same viewer as Photos
    expect((await within(dlg).findByAltText('Snapshot 1')).getAttribute('src')).toBe('/big.jpg')
    expect(within(dlg).queryByRole('button', { name: /favourites/ })).toBeNull()                       // nothing to favourite here
    fireEvent.click(within(dlg).getByRole('button', { name: 'Close photo viewer' }))                   // no animation support: closes at once
    expect(screen.queryByRole('dialog', { name: 'Photo viewer' })).toBeNull()
    expect(document.activeElement).toBe(img)
  })
  it('grows out of the thumbnail when it opens and goes back into it when it closes', async () => {
    const runs = fakeAnimate()
    render(<ZoomImage src="/t.jpg" alt="A" />)
    const img = screen.getByRole('button', { name: 'Enlarge: A' })
    img.getBoundingClientRect = () => box(40, 60, 96, 56) as DOMRect
    fireEvent.click(img)
    const dlg = await viewer()
    await within(dlg).findByAltText('A')
    const grow = runs.find((r) => r.keyframes.length === 2 && String(r.keyframes[1]!.transform) === 'none')
    expect(grow).toBeTruthy()
    expect(String(grow!.keyframes[0]!.transform)).toMatch(/translate\(.*\) scale\(/)
    fireEvent.click(within(dlg).getByRole('button', { name: 'Close photo viewer' }))
    expect(screen.queryByRole('dialog', { name: 'Photo viewer' })).not.toBeNull()                      // still there until it has flown back
    const back = runs.filter((r) => r.keyframes.length === 2 && String(r.keyframes[0]!.transform) === 'none')[0]!
    expect(String(back.keyframes[1]!.transform)).toMatch(/scale\(/)
    act(() => back.finish())
    expect(screen.queryByRole('dialog', { name: 'Photo viewer' })).toBeNull()
  })
  it('Escape closes the viewer', async () => {
    render(<ZoomImage src="/t.jpg" alt="A" />)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Enlarge: A' }), { key: 'Enter' })
    const dlg = await viewer()
    fireEvent(dlg, new Event('cancel', { cancelable: true }))
    expect(screen.queryByRole('dialog', { name: 'Photo viewer' })).toBeNull()
  })
  it('pictures in the same group page between each other', async () => {
    render(<><ZoomImage group="g" src="/1.jpg" alt="One" /><ZoomImage group="g" src="/2.jpg" alt="Two" /><ZoomImage src="/3.jpg" alt="Alone" /></>)
    for (const el of document.querySelectorAll('img')) el.getClientRects = () => [box(0, 0, 10, 10)] as unknown as DOMRectList
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: One' }))
    const dlg = await viewer()
    expect(within(dlg).getByText('1 / 2')).toBeInTheDocument()
    fireEvent.click(within(dlg).getByRole('button', { name: 'Next photo' }))
    expect(await within(dlg).findByText('2 / 2')).toBeInTheDocument()
    expect(await within(dlg).findByAltText('Two')).toBeInTheDocument()
  })
  it('the viewer keeps its keyboard shortcuts (arrows page between the pictures)', async () => {
    render(<><ZoomImage group="k" src="/1.jpg" alt="One" /><ZoomImage group="k" src="/2.jpg" alt="Two" /></>)
    for (const el of document.querySelectorAll('img')) el.getClientRects = () => [box(0, 0, 10, 10)] as unknown as DOMRectList
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: One' }))
    const dlg = await viewer()
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(await within(dlg).findByText('2 / 2')).toBeInTheDocument()
  })
  it('keeps the original click handler working, and does not pass the viewer\'s clicks to the parents', async () => {
    const onClick = vi.fn(), parent = vi.fn()
    render(<div onClick={parent}><ZoomImage src="/t.jpg" alt="A" onClick={onClick} /></div>)
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: A' }))
    expect(onClick).toHaveBeenCalledTimes(1)
    parent.mockClear()
    const dlg = await viewer()
    fireEvent.click(within(dlg).getByRole('button', { name: 'Zoom in' }))
    expect(parent).not.toHaveBeenCalled()
  })
})
