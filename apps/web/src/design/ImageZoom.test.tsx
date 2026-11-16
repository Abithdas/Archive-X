import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ZoomImage } from './ImageZoom'

afterEach(() => { vi.restoreAllMocks(); delete (Element.prototype as { animate?: unknown }).animate })

function fakeAnimate() {
  const runs: Array<{ keyframes: Keyframe[]; options: KeyframeAnimationOptions; finish: () => void }> = []
  ;(Element.prototype as unknown as { animate: unknown }).animate = function (keyframes: Keyframe[], options: KeyframeAnimationOptions) {
    const a = { onfinish: null as null | (() => void), oncancel: null as null | (() => void) }
    runs.push({ keyframes, options, finish: () => a.onfinish?.() })
    return a
  }
  return runs
}

describe('ZoomImage', () => {
  it('is an image you can click or press Enter on, named for what it will do', () => {
    render(<ZoomImage src="/t.jpg" alt="Snapshot 1" className="thumb" />)
    const img = screen.getByRole('button', { name: 'Enlarge: Snapshot 1' })
    expect(img).toHaveClass('thumb'); expect(img).toHaveAttribute('aria-expanded', 'false'); expect(img.tabIndex).toBe(0)
  })
  it('opens bigger with a tilted flight, shows the larger picture, and a second click flies it back', () => {
    const runs = fakeAnimate()
    render(<ZoomImage src="/small.jpg" fullSrc="/big.jpg" alt="Snapshot 1" />)
    const img = screen.getByRole('button', { name: 'Enlarge: Snapshot 1' })
    img.getBoundingClientRect = () => ({ left: 40, top: 60, width: 96, height: 56, right: 136, bottom: 116, x: 40, y: 60, toJSON() {} })
    fireEvent.click(img)
    const dialog = screen.getByRole('dialog', { name: 'Enlarged: Snapshot 1' })
    expect(dialog.querySelector('img')).toHaveAttribute('src', '/big.jpg')
    expect(img).toHaveAttribute('aria-expanded', 'true'); expect(img.style.visibility).toBe('hidden')   // the thumbnail is lifted out while its copy is open
    const open = runs.find((r) => r.keyframes.length === 3)!
    expect(open.keyframes[1]!.transform).toMatch(/rotateY\(-?[1-9]/)                                       // tilted in the middle
    expect(open.keyframes[2]!.transform).toMatch(/scale\(1\.0000\)/)
    fireEvent.click(dialog)                                                                                  // click again
    const back = runs.filter((r) => r.keyframes.length === 3)[1]!
    expect(back.keyframes[0]!.transform).toMatch(/scale\(1\.0000\)/); expect(back.keyframes[2]!.transform).toMatch(/scale\(0\./)   // the same flight in reverse
    expect(screen.queryByRole('dialog')).not.toBeNull()                                                       // still there until it has flown back
    act(() => back.finish())
    expect(screen.queryByRole('dialog')).toBeNull(); expect(img.style.visibility).not.toBe('hidden')
  })
  it('Escape closes it too, and without animation support it simply opens and closes', () => {
    render(<ZoomImage src="/t.jpg" alt="A" />)
    const img = screen.getByRole('button', { name: 'Enlarge: A' })
    fireEvent.keyDown(img, { key: 'Enter' })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('keeps the original click handler working', () => {
    const onClick = vi.fn()
    render(<ZoomImage src="/t.jpg" alt="A" onClick={onClick} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: A' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
  it('opens inside the modal dialog it is shown in (a native dialog hides anything outside it), and on the page otherwise', () => {
    const { container } = render(<dialog open><ZoomImage src="/a.jpg" alt="In dialog" /></dialog>)
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: In dialog' }))
    expect(container.querySelector('dialog .ax-zoom-root')).not.toBeNull()
    expect(document.body.querySelector(':scope > .ax-zoom-root')).toBeNull()
    render(<ZoomImage src="/b.jpg" alt="On page" />)
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: On page' }))
    expect(document.body.querySelector(':scope > .ax-zoom-root')).not.toBeNull()
  })
  it('Escape closes only the enlarged picture, not the dialog under it', () => {
    render(<dialog open><ZoomImage src="/a.jpg" alt="In dialog" /></dialog>)
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge: In dialog' }))
    const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    act(() => { document.dispatchEvent(ev) })
    expect(ev.defaultPrevented).toBe(true)                  // the browser's own "close the dialog" is cancelled
  })
})
