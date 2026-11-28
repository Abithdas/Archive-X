import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enterFullscreen, leaveFullscreen, playClick, playInFullscreen, useBackOnEscape } from './fullscreen'

const setFullscreen = (on: boolean) => {
  Object.defineProperty(document, 'fullscreenElement', { value: on ? document.documentElement : null, configurable: true })
  act(() => { document.dispatchEvent(new Event('fullscreenchange')) })
}
let request: ReturnType<typeof vi.fn>
beforeEach(() => {
  request = vi.fn(async () => undefined)
  document.documentElement.requestFullscreen = request as never
  document.exitFullscreen = vi.fn(async () => { setFullscreen(false) }) as never
})
afterEach(() => { act(() => leaveFullscreen()); setFullscreen(false) })

describe('playing straight into full screen', () => {
  it('asks for full screen in the click that starts the video', () => {
    playInFullscreen()
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('does it for a plain click on a link to a video and leaves the others to the browser', () => {
    const e = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }
    playClick({ ...e, ctrlKey: true }); playClick({ ...e, metaKey: true }); playClick({ ...e, shiftKey: true }); playClick({ ...e, button: 1 })
    expect(request).not.toHaveBeenCalled()
    playClick(e)
    expect(request).toHaveBeenCalledTimes(1)
  })
})

describe('useBackOnEscape', () => {
  it('goes back when full screen ends with Esc after Play opened the page', () => {
    playInFullscreen()
    const back = vi.fn()
    renderHook(() => useBackOnEscape(back))
    setFullscreen(true)
    expect(back).not.toHaveBeenCalled()
    setFullscreen(false)                                 // Esc: the browser ends it, the app did not
    expect(back).toHaveBeenCalledTimes(1)
  })
  it('stays on the page when the player\'s own full screen button ends it', () => {
    playInFullscreen()
    const back = vi.fn()
    renderHook(() => useBackOnEscape(back))
    setFullscreen(true)
    act(() => leaveFullscreen())
    expect(back).not.toHaveBeenCalled()
  })
  it('does nothing on a page that was not opened by Play (full screen from the player\'s button, then Esc)', () => {
    const back = vi.fn()
    renderHook(() => useBackOnEscape(back))
    act(() => enterFullscreen())
    setFullscreen(true)
    setFullscreen(false)
    expect(back).not.toHaveBeenCalled()
  })
  it('does nothing when full screen was refused and never began', () => {
    request.mockRejectedValueOnce(new Error('denied'))
    playInFullscreen()
    const back = vi.fn()
    renderHook(() => useBackOnEscape(back))
    expect(back).not.toHaveBeenCalled()
  })
})
