import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { DatabasePanel } from './DatabasePanel'

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), toasts), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const STATE = { ok: true, file_bytes: 837_000_000, free_bytes: 154_000_000, free_percent: 18.4, incremental: false, last_compact: '', last_optimize: '', can_compact: true, compact_needs_bytes: 1_100_000_000, disk_free_bytes: 9e9, compact_recommended: true }
type Call = { url: string; method: string }
const mount = (state: Record<string, unknown> = STATE, compact: () => Response = () => json({ ok: true, seconds: 3, saved_bytes: 300_000_000, before_bytes: 1, after_bytes: 1 })) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET' })
    if (url === '/api/settings/database/compact') return compact()
    if (url === '/api/settings/database') return json(state)
    return json({ ok: true })
  }))
  withRouter(<DatabasePanel />)
  return calls
}
afterEach(() => { vi.unstubAllGlobals(); toasts.success.mockClear(); toasts.error.mockClear() })

describe('DatabasePanel', () => {
  it('shows the size and the unused space', async () => {
    mount()
    expect(await screen.findByText('Database size')).toBeInTheDocument()
    expect(screen.getByText(/18.4%/)).toBeInTheDocument()
    expect(screen.getByText(/Compacting would give back unused space/)).toBeInTheDocument()
  })

  it('asks before compacting, and does nothing when cancelled', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Compact database' }))
    expect(await screen.findByText(/Writing pauses for a few seconds/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('compacts after the confirmation and reports what was given back', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Compact database' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Compact' }))
    await waitFor(() => expect(toasts.success).toHaveBeenCalled())
    expect(String(toasts.success.mock.calls[0]![0])).toMatch(/Compacted in 3 s/)
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual(['/api/settings/database/compact'])
  })

  it("shows the server's reason when compacting is refused", async () => {
    mount(STATE, () => json({ detail: 'A job is running. Wait for it to finish, or pause it, then compact the database.' }, 409))
    fireEvent.click(await screen.findByRole('button', { name: 'Compact database' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Compact' }))
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(expect.stringMatching(/A job is running/)))
  })

  it('disables the button and says why when there is not enough disk space', async () => {
    mount({ ...STATE, can_compact: false, disk_free_bytes: 100 })
    expect(await screen.findByRole('button', { name: 'Compact database' })).toBeDisabled()
    expect(screen.getByText(/free disk space/)).toBeInTheDocument()
  })
})
