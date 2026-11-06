import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { TransfersPage } from './TransfersPage'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const channels = [{ telegram_id: '-1001', title: 'Main Archive', video_count: 18 }, { telegram_id: '-1002', title: 'Misc', video_count: 4 }]
type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined
const mount = (extra: Handler = () => undefined, jobs: unknown[] = []) => {
  const calls: Array<{ url: string; body: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const x = await extra(url, init)
    if (x) return x
    if (url === '/api/telegram-transfer/lanes') return json({ governor: { transfer_active: 1, transfer_capacity: 3 }, resources: { background_active: 0, background_capacity: 2, media_workers: 2 }, jobs })
    if (url === '/api/channels') return json(channels)
    return json({})
  }))
  withRouter(<TransfersPage />)
  return calls
}

describe('Live lanes', () => {
  it('shows the resource chips and an empty state', async () => {
    mount()
    const chips = await screen.findByRole('list', { name: 'Resource usage' })
    expect(within(chips).getByText('1/3')).toBeInTheDocument()
    expect(await screen.findByText(/No active lane jobs/)).toBeInTheDocument()
  })
  it('lists a running lane job with progress and pauses it', async () => {
    const calls = mount((url) => (url.endsWith('/pause') ? json({ ok: true, status: 'PAUSED' }) : undefined), [{ id: 'j1', type: 'UPLOAD_LOCAL', status: 'RUNNING', operation: 'Uploading video', current_item: 'a.mp4', progress: 40 }])
    expect(await screen.findByText('Uploading a.mp4')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Uploading a.mp4 progress' })).toHaveAttribute('aria-valuenow', '40')
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/j1/pause')).toBe(true))
  })
  it('asks before stopping a job, and only then cancels it', async () => {
    const calls = mount((url) => (url.endsWith('/cancel') ? json({ ok: true, status: 'CANCELLED' }) : undefined), [{ id: 'j2', type: 'TELEGRAM_TRANSFER', status: 'RUNNING', progress: 10 }])
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }))
    expect(calls.some((c) => c.url.endsWith('/cancel'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(calls.some((c) => c.url.endsWith('/cancel'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop job' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/j2/cancel')).toBe(true))
  })
})

describe('Metadata & Assets workflow', () => {
  const toRun = async () => {
    fireEvent.click(await screen.findByRole('checkbox', { name: /Main Archive/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
  }
  it('cannot leave the first step without a channel', async () => {
    mount()
    await screen.findByRole('checkbox', { name: /Main Archive/ })
    expect(screen.getByRole('button', { name: /Next: Processing Stages/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByText('Selected: 2 channels')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.getByText('Selected: 0 channels')).toBeInTheDocument()
  })
  it('runs preflight, then queues the recommended refresh with the right payload', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: true, ready: true }) : url.endsWith('/process-channel') ? json({ ok: true, id: 'job9' }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/metadata-assets/process-channel')).toBe(true))
    const body = calls.find((c) => c.url === '/api/metadata-assets/process-channel')!.body as Record<string, unknown>
    expect(body).toMatchObject({ channel_ids: ['-1001'], stages: ['inventory', 'metadata', 'performers', 'channel_data', 'snapshots'], unbounded: true, force_metadata: false })
    expect(await screen.findByText(/queued as job job9: Inventory → Metadata & Entity Data → Build snapshots/)).toBeInTheDocument()
  })
  it('offers to add required stages when preflight says they are missing', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: true, ready: false, required_stages: ['inventory'], missing_by_channel: [{ channel_id: '-1001', missing: ['inventory'] }] }) : url.endsWith('/process-channel') ? json({ ok: true }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('Add the required stage(s) before running the selected stages?')).toBeInTheDocument()
    expect(calls.some((c) => c.url.endsWith('/process-channel'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Add stages and continue' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/process-channel'))).toBe(true))
    expect((calls.find((c) => c.url.endsWith('/process-channel'))!.body as Record<string, unknown>).auto_added_prerequisites).toEqual({ inventory: ['-1001'] })
  })
  it('reports a failed preflight without starting anything', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: false, detail: 'Telegram is not connected' }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText(/Could not validate prerequisites: Telegram is not connected/)).toBeInTheDocument()
    expect(calls.some((c) => c.url.endsWith('/process-channel'))).toBe(false)
  })
  it('Run Pending skips inventory and never scans', async () => {
    const calls = mount((url) => (url.endsWith('/process-pending') ? json({ ok: true, no_pending: true, message: 'No pending Library items.' }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run Pending (no scan)' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/process-pending'))).toBe(true))
    expect((calls.find((c) => c.url.endsWith('/process-pending'))!.body as { stages: string[] }).stages).not.toContain('inventory')
    expect(calls.some((c) => c.url.endsWith('/preflight'))).toBe(false)
  })
})
