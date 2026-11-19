import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { Shell } from './Shell'
import { setNavMode } from './navMode'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
})
afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
  setNavMode('top')
})

class QuietSocket { onopen = null; onclose = null; onmessage = null; onerror = null; close() {} }

const status = { jobs: [{ type: 'TELEGRAM_TRANSFER', status: 'RUNNING' }, { type: 'MEDIA_MANAGER', status: 'RUNNING' }], telegram_connected: true }
const mount = () => {
  vi.stubGlobal('WebSocket', QuietSocket)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('/api/status') ? status : String(url).includes('storage') ? { bytes: 1, files: 1, disk: {} } : []), { status: 200, headers: { 'content-type': 'application/json' } })))
  return withRouter(<Shell />)
}
/** A stand-in for the desktop app's window commands. */
const desktop = () => {
  const invoke = vi.fn(async (command: string) => (command === 'window_is_maximized' ? false : command === 'window_toggle_maximize' ? true : undefined))
  ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke }
  return invoke
}

describe('the top bar', () => {
  it('is the main menu by default: the pages, with the jobs running on them, and no side menu', async () => {
    mount()
    const nav = await screen.findByRole('navigation', { name: 'Pages' })
    expect(screen.queryByRole('complementary', { name: 'Main' })).toBeNull()
    for (const name of ['Photos', 'Videos', 'Performers', 'Studios', 'Operations', 'Transfers', 'Settings', 'AI Assistance']) {
      expect(within(nav).getByRole('link', { name: new RegExp(name) })).toBeInTheDocument()
    }
    await waitFor(() => expect(within(nav).getByRole('link', { name: /Transfers/ })).toHaveTextContent('2'))
    expect(within(nav).getByRole('link', { name: /Photos/ }).querySelector('[role="status"]')).toBeNull()
  })

  it('gives way to the side menu when the person chooses it', async () => {
    setNavMode('side')
    mount()
    expect(await screen.findByRole('complementary', { name: 'Main' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Pages' })).toBeNull()
  })

  it('has no window buttons in a browser', async () => {
    mount()
    await screen.findByRole('navigation', { name: 'Pages' })
    expect(screen.queryByRole('button', { name: 'Close window' })).toBeNull()
  })

  it('in the desktop app hides the native title bar and shows minimize, maximize and close in the bar', async () => {
    const invoke = desktop()
    mount()
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('window_chrome', { custom: true }))
    fireEvent.click(await screen.findByRole('button', { name: 'Minimize window' }))
    expect(invoke).toHaveBeenCalledWith('window_minimize', undefined)
    fireEvent.click(screen.getByRole('button', { name: 'Maximize window' }))
    expect(invoke).toHaveBeenCalledWith('window_toggle_maximize', undefined)
    fireEvent.click(screen.getByRole('button', { name: 'Close window' }))
    expect(invoke).toHaveBeenCalledWith('window_close', undefined)
  })

  it('moves the window when an empty part of the bar is pressed, and maximizes it on a double press, but not when a button is pressed', async () => {
    const invoke = desktop()
    const { container } = mount()
    await screen.findByRole('button', { name: 'Close window' })
    const bar = container.querySelector('header')!
    fireEvent.mouseDown(bar, { button: 0, detail: 1 })
    expect(invoke).toHaveBeenCalledWith('window_drag', undefined)
    fireEvent.mouseDown(bar, { button: 0, detail: 2 })
    expect(invoke).toHaveBeenCalledWith('window_toggle_maximize', undefined)
    invoke.mockClear()
    fireEvent.mouseDown(screen.getByRole('link', { name: /^Notifications/ }), { button: 0, detail: 1 })
    fireEvent.mouseDown(within(screen.getByRole('navigation', { name: 'Pages' })).getByRole('link', { name: /Photos/ }), { button: 0, detail: 1 })
    expect(invoke).not.toHaveBeenCalledWith('window_drag', undefined)
  })

  it('does not move or maximize the window when text inside the account menu or a dialog is pressed', async () => {
    const invoke = desktop()
    const { container } = mount()
    await screen.findByRole('button', { name: 'Close window' })
    const menu = container.querySelector('[role="menu"]')!
    invoke.mockClear()
    fireEvent.mouseDown(menu.querySelector('div')!, { button: 0, detail: 1 })
    fireEvent.mouseDown(menu.querySelector('div')!, { button: 0, detail: 2 })
    expect(invoke).not.toHaveBeenCalledWith('window_drag', undefined)
    expect(invoke).not.toHaveBeenCalledWith('window_toggle_maximize', undefined)
  })

  it('brings the native title bar back when the side menu is chosen', async () => {
    const invoke = desktop()
    setNavMode('side')
    mount()
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('window_chrome', { custom: false }))
    expect(screen.queryByRole('button', { name: 'Close window' })).toBeNull()
  })
})
