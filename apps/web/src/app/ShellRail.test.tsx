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

describe('sidebar collapse button', () => {
  it('is one button whose two icons swap in place, in the same spot for both states', async () => {
    vi.stubGlobal('WebSocket', QuietSocket)
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('storage') ? { bytes: 1, files: 1, disk: { total: 10, used: 1, free: 9 } } : {}), { status: 200 })))
    withRouter(<Shell />)
    const btn = await screen.findByRole('button', { name: 'Collapse sidebar' })
    const swap = btn.querySelector('.t-icon-swap') as HTMLElement
    expect(swap.dataset.state).toBe('a')
    expect(btn.querySelectorAll('.t-icon')).toHaveLength(2)
    fireEvent.click(btn)
    const same = screen.getByRole('button', { name: 'Expand sidebar' })
    expect(same).toBe(btn)                                  // the same element, so the transition can play
    expect(same.getAttribute('aria-expanded')).toBe('false')
    expect((same.querySelector('.t-icon-swap') as HTMLElement).dataset.state).toBe('b')
    fireEvent.click(same)
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBe(btn)
    expect((btn.querySelector('.t-icon-swap') as HTMLElement).dataset.state).toBe('a')
  })
})
