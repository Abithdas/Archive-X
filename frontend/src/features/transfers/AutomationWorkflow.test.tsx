import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AutomationWorkflow } from './AutomationWorkflow'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const channels = [{ telegram_id: '-1001', title: 'Main Archive', video_count: 18 }]
const SAVED = { rss_url: 'https://feed.example/rss.xml', destination_channel: '-1001', download_path: 'D:\\dl', poll_interval: 300, qbittorrent_username: 'admin', qbittorrent_username_saved: true, qbittorrent_password_saved: false, qbittorrent_password: 'LEAK', only_1080p: true }
type Handler = (url: string, init?: RequestInit) => Response | undefined
const mount = (cfg: Record<string, unknown> = SAVED, ca: Record<string, unknown> = {}, extra: Handler = () => undefined) => {
  const calls: Array<{ url: string; body: Record<string, unknown> | undefined }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    const x = extra(url, init)
    if (x) return x
    if (url === '/api/channels') return json(channels)
    if (url === '/api/operations/automation/config' && !init?.method) return json(cfg)
    if (url === '/api/status') return json({ content_automation: ca })
    return json({ ok: true })
  }))
  withRouter(<AutomationWorkflow />)
  return calls
}
const loaded = () => waitFor(() => expect(screen.getByLabelText('RSS feed URL')).toHaveValue('https://feed.example/rss.xml'))
const pill = (re: RegExp) => fireEvent.click(screen.getByRole('button', { name: re }))
const go = (n: number) => { for (let i = 0; i < n; i++) fireEvent.click(screen.getByRole('button', { name: 'Next' })) }
const posts = (calls: ReturnType<typeof mount>, tail: string) => calls.filter((c) => c.url.endsWith(tail) && c.body !== undefined)

describe('Content Automation', () => {
  it('loads the saved configuration, but never shows a saved password', async () => {
    mount()
    await waitFor(() => expect(screen.getByLabelText('RSS feed URL')).toHaveValue('https://feed.example/rss.xml'))
    expect(screen.getByLabelText('Torrent download folder')).toHaveValue('D:\\dl')
    go(1)
    expect(screen.getByRole('spinbutton')).toHaveValue(300)
    fireEvent.click(screen.getByText('qBittorrent connection'))
    expect(screen.getByLabelText('qBittorrent username')).toHaveValue('admin')
    expect(screen.getByLabelText('qBittorrent password')).toHaveValue('')
    expect(screen.getByText(/password/)).toBeInTheDocument()
  })
  it('will not leave Source without a valid feed and a destination', async () => {
    mount({})
    const next = await screen.findByRole('button', { name: 'Next' })
    expect(next).toBeDisabled()
    fireEvent.change(screen.getByLabelText('RSS feed URL'), { target: { value: 'ftp://nope' } })
    expect(next).toBeDisabled()
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.change(screen.getByLabelText('RSS feed URL'), { target: { value: 'https://x/feed' } })
    await screen.findByRole('option', { name: 'Main Archive' })
    fireEvent.change(screen.getByLabelText('Destination channel'), { target: { value: '-1001' } })
    expect(next).toBeEnabled()
  })
  it('blocks a poll interval under 30 seconds and a min size above the max', async () => {
    mount()
    await waitFor(() => expect(screen.getByLabelText('RSS feed URL')).toHaveValue('https://feed.example/rss.xml'))
    go(1)
    const poll = screen.getByRole('spinbutton')
    fireEvent.change(poll, { target: { value: '10' } })
    expect(screen.getByRole('alert')).toHaveTextContent('between 30 and 3600')
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    fireEvent.change(poll, { target: { value: '60' } })
    go(1)
    const [min, max] = screen.getAllByRole('spinbutton')
    fireEvent.change(min!, { target: { value: '500' } })
    fireEvent.change(max!, { target: { value: '100' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Minimum size cannot exceed maximum size.')
  })
  it('Start saves first, then starts, and reports the job', async () => {
    const calls = mount(SAVED, {}, (url) => (url.endsWith('/start') ? json({ ok: true, id: 'auto7', version: 'v5.0' }) : undefined))
    await waitFor(() => expect(screen.getByLabelText('RSS feed URL')).toHaveValue('https://feed.example/rss.xml'))
    go(5)
    fireEvent.click(screen.getByRole('button', { name: 'Start automation' }))
    expect(await screen.findByText('Started job auto7.')).toBeInTheDocument()
    const order = calls.filter((c) => c.body !== undefined).map((c) => c.url.split('/').pop())
    expect(order.indexOf('config')).toBeLessThan(order.indexOf('start'))
    expect(posts(calls, '/config')[0]!.body).toMatchObject({ rss_url: 'https://feed.example/rss.xml', destination_channel: '-1001', poll_interval: 300, torrent_backend: 'qbittorrent', max_concurrent_torrents: 1 })
    expect(screen.getByText(/Connected · v5\.0/)).toBeInTheDocument()
  })
  it('shows the engine test result, success and failure', async () => {
    mount(SAVED, {}, (url) => (url.endsWith('/test-engine') ? json({ ok: false, detail: 'connection refused' }) : undefined))
    await loaded()
    pill(/6\s*Run/)
    fireEvent.click(screen.getByRole('button', { name: 'Validate qBittorrent' }))
    expect(await screen.findByText('Download engine test failed: connection refused')).toBeInTheDocument()
    expect(screen.getByText('Not connected')).toBeInTheDocument()
  })
  it('Stop calls the stop endpoint', async () => {
    const calls = mount()
    await loaded()
    pill(/6\s*Run/)
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByText('Stopped.')).toBeInTheDocument()
    expect(calls.some((c) => c.url.endsWith('/automation/stop'))).toBe(true)
  })
  it('Clear all asks first and only then clears', async () => {
    const calls = mount(SAVED, {}, (url) => (url.endsWith('/clear-all') ? json({ ok: true, queue_items: 3, torrents: 2 }) : undefined))
    await loaded()
    pill(/4\s*Queue/)
    fireEvent.click(await screen.findByRole('button', { name: 'Clear all' }))
    expect(calls.some((c) => c.url.endsWith('/clear-all'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Keep' }))
    expect(calls.some((c) => c.url.endsWith('/clear-all'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear all' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/clear-all'))).toBe(true))
    expect(await screen.findByText('Cleared 3 queue item(s), 2 torrent(s), and automation cache.')).toBeInTheDocument()
  })
  it('shows the live queue with progress, and asks before skipping a download', async () => {
    const ca = { counts: { QUEUED: 1 }, items: [{ item_key: 'k1', title: 'Release One', status: 'DOWNLOADING', download_progress: 0.4, download_rate: 1024 ** 2, eta_seconds: 60 }, { item_key: 'k2', title: 'Release Two', status: 'QUEUED' }] }
    const calls = mount(SAVED, ca, (url) => (url.endsWith('/skip-item') ? json({ ok: true, title: 'Release One' }) : undefined))
    await loaded()
    pill(/4\s*Queue/)
    expect(await screen.findByText('Release One')).toBeInTheDocument()
    expect(screen.getByText(/DOWNLOAD · 40%/)).toBeInTheDocument()
    expect(screen.getByText(/1\.0 MB\/s · ETA 1m 0s/)).toBeInTheDocument()
    expect(screen.getByText('Release Two')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(calls.some((c) => c.url.endsWith('/skip-item'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Skip download' }))
    await waitFor(() => expect(posts(calls, '/skip-item')[0]!.body).toEqual({ item_key: 'k1' }))
  })
  it('offers to resume or cancel a task that was interrupted by closing the app', async () => {
    const calls = mount(SAVED, { restart_pending: { id: 1 }, items: [] }, (url) => (url.endsWith('/resume') ? json({ ok: true }) : undefined))
    await loaded()
    pill(/4\s*Queue/)
    expect(await screen.findByText('Previous Content Automation task is paused')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resume previous task' }))
    expect(await screen.findByText('Previous Content Automation task resumed.')).toBeInTheDocument()
    expect(calls.some((c) => c.url.endsWith('/automation/resume'))).toBe(true)
  })
})
