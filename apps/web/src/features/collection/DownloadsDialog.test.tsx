import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { DownloadsDialog, downloadsOf, sizeOf } from './DownloadsDialog'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })

const SETTINGS = { on: false, dir: 'C:/Videos/Archive X Collection', quality: 1080, max_gb_movie: 12, max_gb_episode: 4 }
const item = (o: Record<string, unknown>) => ({ season: 0, episode: 0, name: '', label: 'The End of Oak Street', air_date: '', release: '1080p Web · Torrentio', note: '', got: 0, total: 0, attempts: 1, ...o })
const MOVIE = { id: 1, kind: 'movie', imdb: 'tt1', title: 'The End of Oak Street', year: 2026, poster: '', scope: 'all', monitored: false, manual: true, counts: { WANTED: 0, DOWNLOADING: 1, DONE: 0, SKIPPED: 0 }, items: [item({ id: 9, state: 'DOWNLOADING', got: 322_122_548, total: 1_610_612_736 })] }
const SHOW = { id: 2, kind: 'series', imdb: 'tt2', title: 'Dark', year: 2017, poster: '', scope: 'all', monitored: true, counts: { WANTED: 1, DOWNLOADING: 0, DONE: 1, SKIPPED: 0, FAILED: 1 },
  items: [item({ id: 21, season: 1, episode: 1, label: 'S01E01 · Secrets', state: 'DONE', got: 900_000_000, total: 900_000_000 }), item({ id: 22, season: 1, episode: 2, label: 'S01E02 · Lies', state: 'FAILED', note: 'The download stalled.' }), item({ id: 23, season: 1, episode: 3, label: 'S01E03', state: 'WANTED' })] }

function serve(titles: unknown[], server = { up: true, ready: true, installed: false }) {
  const calls: Array<{ url: string; method: string; body?: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url === '/api/collection') return json({ settings: SETTINGS, titles, server, running: false })
    return json({ ok: true, settings: SETTINGS })
  }))
  return calls
}

describe('what the window lists', () => {
  it('lists what is downloading, failed or saved, and not what only waits', () => {
    const groups = downloadsOf([MOVIE, SHOW] as never)
    expect(groups.map((g) => [g.title.title, g.items.map((i) => i.state)])).toEqual([['The End of Oak Street', ['DOWNLOADING']], ['Dark', ['DONE', 'FAILED']]])
    expect(sizeOf(1_610_612_736)).toBe('1.5 GB')
    expect(sizeOf(0)).toBe('')
  })
})

describe('DownloadsDialog', () => {
  it('shows the count in its tab, the progress of a running download and where files are saved', async () => {
    serve([MOVIE, SHOW])
    withRouter(<DownloadsDialog open onClose={() => {}} />)
    const dialog = await screen.findByRole('dialog', { name: 'Downloads' })
    expect(await within(dialog).findByRole('tab', { name: 'Downloads (3)' })).toHaveAttribute('aria-selected', 'true')
    expect(within(dialog).getByRole('progressbar', { name: 'The End of Oak Street download' })).toHaveAttribute('aria-valuenow', '20')
    expect(within(dialog).getByText('Downloading · 20% · of 1.5 GB')).toBeInTheDocument()
    expect(within(dialog).getByText('Dark · S01E01 · Secrets')).toBeInTheDocument()
    expect(within(dialog).getByText('Failed · The download stalled.')).toBeInTheDocument()
    expect(within(dialog).getByText('Saved to C:/Videos/Archive X Collection')).toBeInTheDocument()
  })
  it('says what to do when nothing is downloaded', async () => {
    serve([])
    withRouter(<DownloadsDialog open onClose={() => {}} />)
    expect(await screen.findByText(/Nothing downloaded yet\. Press the download button on a stream/)).toBeInTheDocument()
  })
  it('deletes one download and tries a failed one again', async () => {
    const calls = serve([MOVIE, SHOW])
    withRouter(<DownloadsDialog open onClose={() => {}} />)
    const dialog = await screen.findByRole('dialog', { name: 'Downloads' })
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Delete The End of Oak Street' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/collection/items/9' && c.method === 'DELETE')).toBe(true))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/collection/items/22/retry')?.method).toBe('POST'))
  })
  it('warns when the streaming server is not running while something is listed', async () => {
    serve([MOVIE], { up: false, ready: false, installed: false })
    withRouter(<DownloadsDialog open onClose={() => {}} />)
    expect(await screen.findByText(/Downloads need Stremio's streaming server, which is not running/)).toBeInTheDocument()
  })
  it('has the folder and quality under Settings, and saves a new folder', async () => {
    const calls = serve([])
    withRouter(<DownloadsDialog open onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Settings' }))
    expect(await screen.findByRole('combobox', { name: 'Quality' })).toHaveValue('1080')
    const input = screen.getByLabelText('Download folder')
    expect(input).toHaveAttribute('placeholder', 'C:/Videos/Archive X Collection')
    fireEvent.change(input, { target: { value: 'D:/Films' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change…' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/collection/settings')?.body).toEqual({ dir: 'D:/Films' }))
  })
})
