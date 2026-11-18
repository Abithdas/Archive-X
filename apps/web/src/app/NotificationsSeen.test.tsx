import { fireEvent, screen, waitFor } from '@testing-library/react'
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

const now = () => Math.floor(Date.now() / 1000)
const events = (n: number) => Array.from({ length: n }, (_, i) => ({ level: 'ERROR', event: 'telegram.send.failed', message: `Telegram send failed for video ${i}`, created_at: now() - i * 60 }))

describe('the bell count', () => {
  it('counts new notices and drops to nothing once the list has been opened', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('storage') ? { bytes: 1, files: 1, disk: { total: 10, used: 1, free: 9 } } : String(url).includes('/api/status') ? { events: events(3), jobs: [] } : {}), { status: 200 })))
    const { container } = withRouter(<Shell />)
    const badge = () => container.querySelector('.ax-bell .mo-badge') as HTMLElement
    await waitFor(() => expect(badge().dataset.open).toBe('true'))
    expect(badge().textContent).toBe('3')
    fireEvent.click(screen.getByRole('button', { name: /^Notifications, 3 new/ }))
    await waitFor(() => expect(badge().dataset.open).toBe('false'))
    expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('ax_seen_notices') || '[]').length).toBeGreaterThan(0)   // remembered for the next visit
  })
})
