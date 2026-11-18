import { fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { Shell } from './Shell'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
})
afterEach(() => { vi.unstubAllGlobals(); try { localStorage.clear() } catch { /* none */ } })

class QuietSocket { onopen = null; onclose = null; onmessage = null; onerror = null; close() {} }

const quietFetch = (jobs: unknown[] = [], version?: string) => vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('storage') ? { bytes: 1, files: 1, disk: { total: 10, used: 1, free: 9 } } : String(url).includes('/api/status') ? { jobs, version } : {}), { status: 200 }))

describe('sidebar collapse button', () => {
  it('swaps a hamburger (minimized) and an X (open) in the same button, with the icon swap transition', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', quietFetch())
    withRouter(<Shell />)
    const btn = await screen.findByRole('button', { name: 'Collapse sidebar' })
    const icon = (which: 'a' | 'b') => btn.querySelector(`.mo-glyph[data-icon="${which}"] use`)?.getAttribute('href')
    const swap = () => (btn.querySelector('.mo-swap') as HTMLElement).dataset.state
    expect(icon('a')).toMatch(/#ax-menu$/)       // the hamburger is shown while minimized
    expect(icon('b')).toMatch(/#ax-x$/)      // the X is shown while open
    expect(swap()).toBe('b')                     // open: the X
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(btn)
    const same = screen.getByRole('button', { name: 'Expand sidebar' })
    expect(same).toBe(btn)
    expect(same.getAttribute('aria-expanded')).toBe('false')
    expect(swap()).toBe('a')                     // minimized: the hamburger
    fireEvent.click(same)
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBe(btn)
    expect(swap()).toBe('b')
  })

  it('comes before the logo in the sidebar header', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', quietFetch())
    withRouter(<Shell />)
    const btn = await screen.findByRole('button', { name: 'Collapse sidebar' })
    const logo = btn.parentElement!.querySelector('img')!
    expect(btn.compareDocumentPosition(logo) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('puts a running-jobs count on the corner of its icon when collapsed (it must not be a relative element, which cancels absolute)', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', quietFetch([{ type: 'METADATA_SYNC', status: 'RUNNING' }]))
    withRouter(<Shell />)
    fireEvent.click(await screen.findByRole('button', { name: 'Collapse sidebar' }))
    const badge = (await screen.findAllByRole('status', { name: '1 running' }))[0]!
    expect(badge.className).toMatch(/\babsolute\b/)
    expect(badge.className).not.toMatch(/\brelative\b/)
  })

  it('shows the version under the storage card, and not when the sidebar is folded', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', quietFetch([], '30.3.2'))
    withRouter(<Shell />)
    const line = await screen.findByText('Archive X v30.3.2')
    const storage = screen.getByTitle('Archive X app storage summary')
    expect(storage.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()   // below it
    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(screen.queryByText('Archive X v30.3.2')).toBeNull()
  })
})
