import { screen, waitFor, within } from '@testing-library/react'
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
afterEach(() => { vi.unstubAllGlobals(); setNavMode('top') })

class QuietSocket { onopen = null; onclose = null; onmessage = null; onerror = null; close() {} }

const status = { jobs: [
  { type: 'TELEGRAM_TRANSFER', status: 'RUNNING' },
  { type: 'MEDIA_MANAGER', status: 'RUNNING' },
  { type: 'METADATA_CHANNEL_PIPELINE', status: 'RUNNING' },
  { type: 'UPLOAD_LOCAL', status: 'PAUSED' },
] }

describe('sidebar activity counters', () => {
  it('shows how many jobs are running on the pages they belong to, and nothing on idle pages', async () => {
    setNavMode('side')
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('/api/status') ? status : String(url).includes('storage') ? { bytes: 10, files: 3, disk: { total: 1000, used: 100, free: 900 } } : {}), { status: 200 })))
    withRouter(<Shell />)
    const side = await screen.findByRole('complementary', { name: 'Main' })
    const link = (name: RegExp) => within(side).getByRole('link', { name })
    await waitFor(() => expect(link(/Transfers/)).toHaveTextContent('2'))
    expect(link(/Operations/)).toHaveTextContent('3')
    expect(link(/Videos/)).toHaveTextContent('1')
    expect(link(/Photos/).querySelector('[role="status"]')).toBeNull()
    expect(link(/Settings/).querySelector('[role="status"]')).toBeNull()
    expect(link(/Transfers/).querySelector('[aria-label="2 running"]')).not.toBeNull()
    // the phone bar shows the same counts on its five main pages
    const phone = screen.getAllByRole('navigation', { name: 'Main' }).at(-1)!
    expect(within(phone).getByRole('link', { name: /Transfers/ })).toHaveTextContent('2')
    expect(within(phone).getByRole('link', { name: /Performers/ }).querySelector('[role="status"]')).toBeNull()
  })
})

describe('the top bar', () => {
  const mountShell = (telegram: boolean) => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('/api/status') ? { jobs: [], telegram_connected: telegram } : String(url).includes('storage') ? { bytes: 1, files: 1, disk: { total: 10, used: 1, free: 9 } } : {}), { status: 200 })))
    withRouter(<Shell />)
  }
  it('has no status dot on the profile picture', async () => {
    mountShell(true)
    const account = await screen.findByRole('button', { name: 'Account menu' })
    await waitFor(() => expect(screen.getByText('Connected')).toBeInTheDocument())
    expect(account.querySelector('i, .ax-live-dot')).toBeNull()
  })
  it('flies the Telegram plane while Telegram is live, and keeps it still when it is not', async () => {
    mountShell(true)
    await waitFor(() => expect(screen.getByText('Connected')).toBeInTheDocument())
    expect(screen.getByText('Connected').closest('a')!.querySelector('.ax-plane')).toHaveAttribute('data-live', 'true')
  })
  it('keeps the plane still when Telegram is not connected', async () => {
    mountShell(false)
    await waitFor(() => expect(screen.getByText('Setup needed')).toBeInTheDocument())
    expect(screen.getByText('Setup needed').closest('a')!.querySelector('.ax-plane')).toHaveAttribute('data-live', 'false')
  })
})
