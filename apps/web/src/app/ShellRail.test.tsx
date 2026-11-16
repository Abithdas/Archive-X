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

const quietFetch = (jobs: unknown[] = []) => vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('storage') ? { bytes: 1, files: 1, disk: { total: 10, used: 1, free: 9 } } : String(url).includes('/api/status') ? { jobs } : {}), { status: 200 }))

describe('sidebar collapse button', () => {
  it('is a hamburger that folds the sidebar and opens it again, always the same button', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', quietFetch())
    withRouter(<Shell />)
    const btn = await screen.findByRole('button', { name: 'Collapse sidebar' })
    expect(btn.querySelector('use')?.getAttribute('href')).toMatch(/#ax-menu$/)   // the three-line icon, not an arrow
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(btn)
    const same = screen.getByRole('button', { name: 'Expand sidebar' })
    expect(same).toBe(btn)
    expect(same.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(same)
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBe(btn)
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
})
