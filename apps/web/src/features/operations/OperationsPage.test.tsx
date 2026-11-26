import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { OperationsPage } from './OperationsPage'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())

const status = {
  telegram_connected: false,
  stats: { failed: 2, media: 72 },
  governor: { max_ranges: 8, max_rpc: 1 },
  jobs: [
    { id: 'a', name: 'Index channel', status: 'RUNNING', completed: 5, total: 20, operation: 'Scanning' },
    { id: 'b', name: 'Upload batch', status: 'QUEUED' },
    { id: 'c', name: 'Old run', status: 'FAILED' },
  ],
  events: [
    { level: 'WARN', module: 'startup', message: 'Telegram API credentials are missing', created_at: '2026-11-06T10:00:00Z' },
    { level: 'INFO', module: 'http', message: 'Web login succeeded', created_at: '2026-11-06T10:01:00Z' },
  ],
}
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const mount = (other?: { count: number; running: number }) => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/status') return json(other ? { ...status, other_mode_jobs: other } : status)
    if (url === '/api/diagnostics/summary') return json({ levels: { ERROR: 1 }, diagnostic_health: { state: 'ATTENTION' } })
    if (url === '/api/operations/resources') return json({ resources: { background_capacity: 2, control: { mode_label: 'Automatic' }, category_active: {} } })
    if (url.startsWith('/api/performers') || url.startsWith('/api/studios')) return json([{ id: 1, name: 'x', video_count: 1 }])
    return json({})
  }))
  return withRouter(<OperationsPage />)
}

describe('OperationsPage', () => {
  it('says how many tasks the other mode has going, without listing them', async () => {
    mount({ count: 1, running: 1 })
    expect(await screen.findByRole('status', { name: 'Tasks in the other mode' })).toHaveTextContent('A task is running in the other mode.')
  })
  it('lists the jobs in one column when one job is shown and in two when more are', async () => {
    mount()
    await screen.findByText('Index channel')
    const list = () => screen.getByText('Index channel').closest('ul')!
    expect(list().className).toContain('md:grid-cols-2')                       // three jobs
    fireEvent.click(screen.getByRole('button', { name: 'Queued' }))
    await waitFor(() => expect(screen.queryByText('Index channel')).not.toBeInTheDocument())
    expect(screen.getByText('Upload batch').closest('ul')!.className).not.toContain('md:grid-cols-2')   // the one queued job fills the row
  })
  it('shows the job counters and the live jobs, and filters them', async () => {
    mount()
    const summary = await screen.findByRole('group', { name: 'Job summary' })
    expect(within(summary).getByText('Running').previousSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Failed').previousSibling).toHaveTextContent('1')
    expect(screen.getByText('Index channel')).toBeInTheDocument()
    expect(screen.getByText((_, el) => el?.tagName === 'P' && el.textContent === '25% · 5 / 20')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Queued' }))
    await waitFor(() => expect(screen.queryByText('Index channel')).not.toBeInTheDocument())   // leaves with a short exit animation
    expect(screen.getByText('Upload batch')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Completed' }))
    expect(await screen.findByText('No jobs match this filter.')).toBeInTheDocument()
  })
  it('explains what needs attention and shows system health', async () => {
    mount()
    const att = await screen.findByRole('region', { name: 'Attention' })
    expect(within(att).getByText(/Telegram is offline/)).toBeInTheDocument()
    expect(within(att).getByText('2 failed jobs require review.')).toBeInTheDocument()
    const health = screen.getByRole('region', { name: 'System health' })
    expect(within(health).getByText('Offline')).toBeInTheDocument()
    expect(within(health).getByText('Waiting for Telegram')).toBeInTheDocument()
  })
  it('loads diagnostics and the resource controller from their own endpoints', async () => {
    mount()
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Diagnostics' })).getByText('ATTENTION')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('list', { name: 'Resource lanes' })).toBeInTheDocument())
  })
  it('filters the activity log by level and text', async () => {
    mount()
    const log = await screen.findByRole('region', { name: 'Recent activity' })
    expect(within(log).getByText('Web login succeeded')).toBeInTheDocument()
    fireEvent.click(within(log).getByRole('button', { name: 'Warning' }))
    expect(within(log).queryByText('Web login succeeded')).not.toBeInTheDocument()
    expect(within(log).getByText(/credentials are missing/)).toBeInTheDocument()
    fireEvent.click(within(log).getByRole('button', { name: 'All' }))
    fireEvent.change(within(log).getByLabelText('Search visible logs'), { target: { value: 'zzz' } })
    expect(within(log).getByText('No log entries match.')).toBeInTheDocument()
  })
  it('asks before resetting HTTP telemetry', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Reset HTTP window' }))
    expect(screen.getByText(/clears only the in-memory HTTP request counters/)).toBeInTheDocument()
    const f = vi.mocked(fetch)
    expect(f.mock.calls.some((c) => String(c[0]).includes('/metrics/reset'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  })
  it('offers the logs and diagnostics as one zip from Recent activity', async () => {
    const calls: string[] = []
    const base = vi.fn(async (url: string) => {
      calls.push(url)
      if (url === '/api/diagnostics/logs.zip') return new Response(new Blob(['PK']), { status: 200, headers: { 'Content-Disposition': 'attachment; filename="archive-x-logs-2026-11-14_08-12-45.zip"' } })
      if (url === '/api/status') return json(status)
      if (url === '/api/diagnostics/summary') return json({ levels: {}, diagnostic_health: { state: 'OK' } })
      if (url === '/api/operations/resources') return json({ resources: { background_capacity: 2, control: { mode_label: 'Automatic' }, category_active: {} } })
      return json({})
    })
    vi.stubGlobal('fetch', base)
    Object.assign(URL, { createObjectURL: () => 'blob:logs', revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    withRouter(<OperationsPage />)
    const log = await screen.findByRole('region', { name: 'Recent activity' })
    fireEvent.click(within(log).getByRole('button', { name: 'Download logs (.zip)' }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(calls).toContain('/api/diagnostics/logs.zip')
    await waitFor(() => expect(within(log).getByRole('button', { name: 'Download logs (.zip)' })).toBeEnabled())   // ready for another one
    click.mockRestore()
  })
})
