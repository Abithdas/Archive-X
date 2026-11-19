import { afterEach, describe, expect, it, vi } from 'vitest'
import { installExternalLinks } from './externalLinks'

installExternalLinks()
afterEach(() => { delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__; document.body.innerHTML = '' })

const link = (href: string) => { const a = document.createElement('a'); a.href = href; a.target = '_blank'; a.textContent = 'x'; document.body.append(a); return a }
const click = (a: HTMLElement) => { const ev = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }); a.dispatchEvent(ev); return ev }

describe('links to other websites in the desktop app', () => {
  it('are opened by the app in the browser, not in the window', () => {
    const invoke = vi.fn(() => Promise.resolve())
    ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke }
    const ev = click(link('https://www.netflix.com/search?q=dune'))
    expect(ev.defaultPrevented).toBe(true)
    expect(invoke).toHaveBeenCalledWith('open_external', { url: 'https://www.netflix.com/search?q=dune' })
  })
  it('leave the app\'s own pages and a browser alone', () => {
    const invoke = vi.fn(() => Promise.resolve())
    ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke }
    expect(click(link('/videos')).defaultPrevented).toBe(false)
    expect(invoke).not.toHaveBeenCalled()
    delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
    expect(click(link('https://example.com')).defaultPrevented).toBe(false)   // no desktop app: the browser does it
  })
})
