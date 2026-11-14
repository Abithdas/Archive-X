import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { JobControls } from './JobControls'

const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }))
vi.mock('sonner', () => ({ toast, Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear() })
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (type: string, replies: Array<Record<string, unknown>>, over: (u: string, i?: RequestInit) => Response | undefined = () => undefined) => {
  const calls: Call[] = []
  let n = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init); if (o) return o
    if (url.startsWith('/api/telegram-transfer?since=')) return json(replies[Math.min(n++, replies.length - 1)])
    return json({ ok: true })
  }))
  withRouter(<JobControls j={{ id: 'job1', type, status: 'RUNNING', name: 'Transfer' }} />)
  return calls
}
const open = async () => { fireEvent.click(await screen.findByRole('button', { name: 'Open console' })); return screen.findByRole('dialog') }

describe('Open console', () => {
  it('is offered only for Telegram manager jobs', async () => {
    mount('AUTOMATION', [{}])
    await screen.findByRole('button', { name: 'Stop' })
    expect(screen.queryByRole('button', { name: 'Open console' })).not.toBeInTheDocument()
  })
  it('shows the title, progress, output and the live operation, adding only new lines', async () => {
    const calls = mount('TELEGRAM_TRANSFER', [
      { running: true, mode_name: 'Transfer job', seq: 2, lines: [{ n: 1, t: 'Starting' }, { n: 2, t: 'Copying 1' }], completed: 1, total: 4, operation: 'Copying video' },
      { running: true, mode_name: 'Transfer job', seq: 3, lines: [{ n: 3, t: 'Copying 2' }], completed: 2, total: 4, operation: 'Copying video' },
    ])
    const dlg = await open()
    expect(await within(dlg).findByText('Running · 1 / 4 — 25%')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Telegram Job Manager — Transfer job' })).toBeInTheDocument()
    expect(within(dlg).getByLabelText('CLI output')).toHaveTextContent('Starting Copying 1')
    expect(within(dlg).getByText('Copying video')).toBeInTheDocument()
    await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/telegram-transfer?since=2'), { timeout: 3000 })
    await waitFor(() => expect(within(dlg).getByLabelText('CLI output').textContent).toBe('Starting\nCopying 1\nCopying 2'), { timeout: 3000 })
  })
  it('answers a question from the CLI, and reports when it is no longer waiting', async () => {
    let first = true
    const calls = mount('MEDIA_MANAGER', [{ running: true, prompt: 'Choose source (1-3):', seq: 0, lines: [] }], (u, i) => (u === '/api/telegram-transfer/input' && i?.method === 'POST' ? (first ? ((first = false), json({ ok: true })) : json({ ok: false, detail: 'Nothing is waiting' })) : undefined))
    const dlg = await open()
    expect(await within(dlg).findByText('Choose source (1-3):')).toBeInTheDocument()
    const send = within(dlg).getByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()
    fireEvent.change(within(dlg).getByLabelText('Answer'), { target: { value: ' 2 ' } })
    fireEvent.click(send)
    await waitFor(() => expect(calls.find((c) => c.url === '/api/telegram-transfer/input')!.body).toEqual({ text: '2' }))
    await waitFor(() => expect(within(dlg).getByLabelText('Answer')).toHaveValue(''))
    fireEvent.change(within(dlg).getByLabelText('Answer'), { target: { value: '3' } }); fireEvent.keyDown(within(dlg).getByLabelText('Answer'), { key: 'Enter' })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Nothing is waiting'))
    expect(within(dlg).getByLabelText('Answer')).toHaveValue('3')
  })
  it('stops the job only after confirmation', async () => {
    const calls = mount('MEDIA_MANAGER', [{ running: true, live: 'Working', seq: 0, lines: [] }])
    const dlg = await open()
    fireEvent.click(await within(dlg).findByRole('button', { name: 'Stop' }))
    const ask = await screen.findByRole('dialog', { name: 'Stop Telegram job' })
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    expect(calls.filter((c) => c.url === '/api/telegram-transfer/stop')).toHaveLength(0)
    fireEvent.click(within(dlg).getByRole('button', { name: 'Stop' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Stop Telegram job' })).getByRole('button', { name: 'Stop job' }))
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/telegram-transfer/stop' && c.method === 'POST')).toHaveLength(1))
  })
  it('shows a finished or errored job plainly, with no Stop button', async () => {
    mount('MEDIA_MANAGER', [{ running: false, error: 'Session expired', seq: 5, lines: [{ n: 5, t: 'bye' }] }])
    const dlg = await open()
    expect(await within(dlg).findByText('Stopped with an error: Session expired')).toBeInTheDocument()
    expect(within(dlg).getByText('Media Manager stopped')).toBeInTheDocument()
    expect(within(dlg).queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
  })
})

describe('Open console with no CLI output', () => {
  it('shows the job activity log instead, with a note saying why', async () => {
    mount('TELEGRAM_TRANSFER', [{ running: false, seq: 0, lines: [] }], (url) => url.startsWith('/api/jobs/job1') ? json({ activity: [{ id: 2, level: 'WARN', message: 'Retrying chunk' }, { id: 1, level: 'INFO', message: 'Started transfer' }] }) : undefined)
    const dlg = await open()
    await waitFor(() => expect(within(dlg).getByLabelText('CLI output')).toHaveTextContent('Started transfer'))
    expect(within(dlg).getByLabelText('CLI output').textContent).toMatch(/INFO\s+Started transfer\nWARN\s+Retrying chunk/)
    expect(within(dlg).getByText(/interactive CLI has no output for this job/)).toBeInTheDocument()
  })
  it('keeps the real CLI output when there is some', async () => {
    mount('TELEGRAM_TRANSFER', [{ running: true, seq: 1, lines: [{ n: 1, t: 'Real CLI line' }] }], (url) => url.startsWith('/api/jobs/job1') ? json({ activity: [{ id: 1, message: 'Should not show' }] }) : undefined)
    const dlg = await open()
    await waitFor(() => expect(within(dlg).getByLabelText('CLI output')).toHaveTextContent('Real CLI line'))
    expect(within(dlg).getByLabelText('CLI output')).not.toHaveTextContent('Should not show')
    expect(within(dlg).queryByText(/interactive CLI has no output/)).not.toBeInTheDocument()
  })
})
