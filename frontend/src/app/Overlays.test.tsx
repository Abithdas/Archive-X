import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AccountMenu } from './AccountMenu'
import { CommandPalette } from './CommandPalette'
import { useUi } from './ui'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
})
afterEach(() => { vi.unstubAllGlobals(); act(() => useUi.setState({ palette: false })) })

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

describe('CommandPalette', () => {
  const open = async () => {
    withRouter(<CommandPalette />)
    act(() => useUi.getState().setPalette(true))
    return screen.findByRole('combobox', { name: 'Search commands' })
  }
  it('lists the nine classic commands', async () => {
    await open()
    expect(screen.getAllByRole('option')).toHaveLength(9)
    expect(screen.getByRole('option', { name: /Browse videos/ })).toHaveAttribute('aria-selected', 'true')
  })
  it('filters as you type and says so when nothing matches', async () => {
    const box = await open()
    fireEvent.change(box, { target: { value: 'perf' } })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    fireEvent.change(box, { target: { value: 'zzzz' } })
    expect(screen.getByText('No matching commands')).toBeInTheDocument()
  })
  it('moves with the arrow keys, wrapping around, and Enter runs the command and closes', async () => {
    const box = await open()
    fireEvent.keyDown(box, { key: 'ArrowUp' })
    expect(screen.getByRole('option', { name: /Refresh library/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: /Browse videos/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(useUi.getState().palette).toBe(false))
  })
  it('"Open Notifications" opens the panel', async () => {
    const box = await open()
    fireEvent.change(box, { target: { value: 'notifications' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(useUi.getState().notifications).toBe(true))
    act(() => useUi.setState({ notifications: false }))
  })
})

describe('AccountMenu', () => {
  it('shows the initial and offers Sign out and Exit app in a browser', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url === '/desktop/info' ? json({ detail: 'x' }, 404) : url === '/api/account/profile' ? json({ name: 'Sam' }) : json({}))))
    withRouter(<AccountMenu live="live" />)
    const btn = await screen.findByRole('button', { name: 'Account menu' })
    await waitFor(() => expect(btn).toHaveTextContent('S'))
    expect(await screen.findByRole('menuitem', { name: 'Sign out', hidden: true })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Exit app/, hidden: true })).toBeInTheDocument()
  })
  it('hides Sign out inside the desktop app', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url === '/desktop/info' ? json({ desktop: true }) : url === '/api/account/profile' ? json({ name: 'Sam' }) : json({}))))
    withRouter(<AccountMenu live="live" />)
    await screen.findByRole('button', { name: 'Account menu' })
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: 'Sign out', hidden: true })).not.toBeInTheDocument())
  })
  it('asks before exiting, and only then calls the backend', async () => {
    const f = vi.fn(async (url: string) => (url === '/api/app/exit' ? json({ ok: true }) : url === '/desktop/info' ? json({ desktop: true }) : json({})))
    vi.stubGlobal('fetch', f)
    withRouter(<AccountMenu live="live" />)
    fireEvent.click(await screen.findByRole('menuitem', { name: /Exit app/, hidden: true }))
    expect(f.mock.calls.some((c) => c[0] === '/api/app/exit')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Keep Archive X open' }))
    expect(f.mock.calls.some((c) => c[0] === '/api/app/exit')).toBe(false)
    fireEvent.click(screen.getByRole('menuitem', { name: /Exit app/, hidden: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Exit app' }))
    await waitFor(() => expect(f.mock.calls.some((c) => c[0] === '/api/app/exit')).toBe(true))
    expect(await screen.findByText(/Waiting for the server/)).toBeInTheDocument()
  })
})

describe('Dialog accessibility', () => {
  it('gives every open dialog its own accessible name', async () => {
    const { Dialog } = await import('@/design/Dialog')
    const { render } = await import('@testing-library/react')
    render(<><Dialog open onClose={() => {}} title="First">a</Dialog><Dialog open onClose={() => {}} title="Second">b</Dialog></>)
    expect(screen.getByRole('dialog', { name: 'First' })).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Second' })).toBeInTheDocument()
  })
})
