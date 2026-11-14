import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { JobDetails } from './JobDetails'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const JOB = { name: 'Nightly scan', type: 'METADATA_ASSETS', status: 'RUNNING', total: 200, completed: 50, current_operation: 'Scanning channel A', current_item: 'file_5.mp4', created_at: '2024-01-02T03:04:05Z', updated_at: '2024-01-02T03:05:00Z', worker_id: 'w1', worker_state: 'BUSY', attempt: 1, retry_count: 0, activity: [{ id: 2, level: 'INFO', message: 'Second step', created_at: '2024-01-02T03:04:30Z' }, { id: 3, level: 'ERROR', message: 'Third step failed', details: { path: '/x' } }], activity_has_older: true }
type Call = { url: string; method: string }
const mount = (job: Record<string, unknown> | (() => Response) = {}, extra: (u: string, m: string) => Response | undefined = () => undefined) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method })
    const x = extra(url, method)
    if (x) return x
    if (typeof job === 'function') return job()
    if (url.includes('activity_before_id')) return json({ activity: [{ id: 1, level: 'INFO', message: 'First step' }], activity_has_older: false })
    return json({ ...JOB, ...job })
  }))
  const onClose = vi.fn()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><JobDetails id="J1" onClose={onClose} /></QueryClientProvider>)
  return { calls, onClose }
}
const posts = (c: Call[]) => c.filter((x) => x.method === 'POST')

describe('Job details', () => {
  it('loads with a placeholder, then shows the job', async () => {
    const { calls } = mount()
    expect(screen.getByRole('status', { name: 'Loading job details' })).toBeInTheDocument()
    const d = await screen.findByRole('dialog', { name: 'Nightly scan' })
    expect(await within(d).findByText('50 / 200')).toBeInTheDocument()
    expect(calls[0]!.url).toBe('/api/jobs/J1?activity_limit=40')
    expect(within(d).getByText('Scanning channel A')).toBeInTheDocument()
    expect(within(d).getByText('file_5.mp4')).toBeInTheDocument()
    expect(within(d).getByText('Metadata & Assets · #J1')).toBeInTheDocument()
    expect(within(d).getByRole('progressbar', { name: 'Job progress' })).toHaveAttribute('aria-valuenow', '25')
    expect(within(d).getByText('RUNNING')).toBeInTheDocument()
  })

  it('shows the pipeline with the current stage and a count', async () => {
    mount({ current_operation: 'Verifying delivery' })
    const d = await screen.findByRole('dialog', { name: 'Nightly scan' })
    const pipe = await within(d).findByRole('region', { name: 'Pipeline' })
    expect(pipe).toHaveTextContent('3 / 5 stages')
    expect(within(pipe).getByText('Verification').closest('li')).toHaveAttribute('data-state', 'active')
    expect(within(pipe).getByText('Queued').closest('li')).toHaveAttribute('data-state', 'done')
    expect(within(pipe).getByText('Cleanup').closest('li')).toHaveAttribute('data-state', 'todo')
  })

  it('shows run information for the worker', async () => {
    mount()
    const info = await screen.findByRole('region', { name: 'Run information' })
    expect(info).toHaveTextContent('w1 · BUSY')
    expect(info).toHaveTextContent('#J1')
  })

  it('shows the error when the job has one, and not otherwise', async () => {
    mount({ status: 'FAILED', error: 'Telegram refused: FloodWait' })
    expect(await screen.findByRole('region', { name: 'Error' })).toHaveTextContent('Telegram refused: FloodWait')
  })
  it('has no error section for a healthy job', async () => {
    mount()
    await screen.findByText('50 / 200')
    expect(screen.queryByRole('region', { name: 'Error' })).toBeNull()
  })

  it('lists activity newest first and loads older activity on request', async () => {
    const { calls } = mount()
    const list = await screen.findByRole('list', { name: 'Job activity' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([expect.stringContaining('Third step failed'), expect.stringContaining('Second step')])
    expect(list).toHaveTextContent('ERROR')
    fireEvent.click(await screen.findByRole('button', { name: 'Load older activity' }))
    await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(3))
    expect(within(list).getAllByRole('listitem').at(-1)).toHaveTextContent('First step')
    expect(calls.some((c) => c.url === '/api/jobs/J1?activity_before_id=2&activity_limit=500')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Load older activity' })).toBeNull()   // no more history
  })

  it('says so when there is no activity yet', async () => {
    mount({ activity: [], activity_has_older: false })
    expect(await screen.findByText(/No detailed activity events yet/)).toBeInTheDocument()
  })

  it('shows technical input when the job detail is JSON', async () => {
    mount({ detail: '{"source":"local","paths":["/a"]}' })
    const tech = await screen.findByText('Technical details')
    fireEvent.click(tech)
    expect(screen.getByText(/"source": "local"/)).toBeInTheDocument()
  })

  it('explains when the job cannot be loaded', async () => {
    mount(() => json({ detail: 'Job not found' }, 404))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load job details: Job not found')
  })
})

describe('Job details footer', () => {
  it('a running job can be paused, which closes the drawer once done', async () => {
    const { calls, onClose } = mount({}, (_u, m) => (m === 'POST' ? json({ ok: true, status: 'PAUSED' }) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(posts(calls)).toEqual([{ url: '/api/jobs/J1/pause', method: 'POST' }]))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toast).toHaveBeenCalledWith('Job paused.')
  })

  it('cancel re-reads the job, asks, and only then cancels', async () => {
    const { calls, onClose } = mount({}, (_u, m) => (m === 'POST' ? json({ ok: true, status: 'CANCELLED' }) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel job' }))
    const ask = await screen.findByRole('dialog', { name: 'Stop job' })
    expect(posts(calls)).toHaveLength(0)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(within(ask).getByRole('button', { name: 'Stop job' }))
    await waitFor(() => expect(posts(calls)).toEqual([{ url: '/api/jobs/J1/cancel', method: 'POST' }]))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('cancelling the confirmation keeps the drawer open and sends nothing', async () => {
    const { calls, onClose } = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel job' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Stop job' })).getByRole('button', { name: 'Cancel' }))
    expect(posts(calls)).toHaveLength(0)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('an interrupted job offers recovery', async () => {
    mount({ status: 'INTERRUPTED' })
    expect(await screen.findByRole('button', { name: 'Recover & Resume' })).toBeInTheDocument()
  })

  it('a finished job has no footer actions', async () => {
    mount({ status: 'COMPLETED' })
    await screen.findByText('50 / 200')
    expect(screen.queryByRole('button', { name: 'Cancel job' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
  })

  it('shows a disabled "Cancelling…" while a cancel is under way', async () => {
    mount({ status: 'CANCEL_REQUESTED' })
    expect(await screen.findByRole('button', { name: 'Cancelling…' })).toBeDisabled()
  })
})
