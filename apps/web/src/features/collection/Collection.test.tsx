import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CollectionPage } from './CollectionPage'
import { KeepButton } from './KeepButton'
import { percent, summary } from './useCollection'
import { withRouter } from '@/test/withRouter'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
afterEach(() => vi.unstubAllGlobals())

const SETTINGS = { on: true, dir: 'C:/Videos/Archive X Collection', quality: 1080, max_gb_movie: 12, max_gb_episode: 4 }
const counts = (o: Record<string, number>) => ({ WANTED: 0, DOWNLOADING: 0, DONE: 0, SKIPPED: 0, ...o })
const DARK = { id: 1, kind: 'series', imdb: 'tt5753856', title: 'Dark', year: 2017, poster: '', scope: 'all', monitored: true, counts: counts({ DOWNLOADING: 1, WANTED: 1, DONE: 3 }),
  items: [{ id: 11, season: 1, episode: 4, name: 'Ep 4', label: 'S01E04 · Ep 4', air_date: '2017-12-01', state: 'DOWNLOADING', release: 'Dark S01E04 1080p', note: '', got: 50, total: 200, attempts: 1 },
    { id: 12, season: 1, episode: 5, name: 'Ep 5', label: 'S01E05 · Ep 5', air_date: '2017-12-08', state: 'WANTED', release: '', note: 'No release found yet', got: 0, total: 0, attempts: 2 }] }

function serve(state: Record<string, unknown>) {
  const calls: Array<{ url: string; method: string; body?: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url === '/api/collection') return json({ settings: SETTINGS, titles: [], server: { up: true, ready: true, installed: false }, running: false, ...state })
    if (url === '/api/screen/streaming-server') return json({ up: false, installed: false, ready: false, setup: { phase: 'idle' } })
    return json({ ok: true })
  }))
  return calls
}

describe('summary and progress', () => {
  it('reads like a sentence', () => {
    expect(summary({ kind: 'series', counts: counts({ DOWNLOADING: 2, WANTED: 3, DONE: 4 }) })).toBe('2 downloading · 3 waiting for a release · 4 done')
    expect(summary({ kind: 'movie', counts: counts({ DONE: 1 }) })).toBe('in your library')
    expect(summary({ kind: 'series', counts: counts({}) })).toBe('Nothing missing')
    expect(percent(50, 200)).toBe(25)
    expect(percent(5, 0)).toBeNull()
  })
})

describe('CollectionPage', () => {
  it('says how to start when nothing is kept', async () => {
    serve({})
    withRouter(<CollectionPage />)
    expect(await screen.findByText('Nothing kept yet')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Quality' })).toHaveValue('1080')
  })

  it('lists what is kept with its progress, and each episode can be skipped or looked for again', async () => {
    const calls = serve({ titles: [DARK] })
    withRouter(<CollectionPage />)
    expect(await screen.findByText('Dark')).toBeInTheDocument()
    expect(screen.getByText('1 downloading · 1 waiting for a release · 3 done')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 episodes' }))
    const list = within(screen.getByRole('list', { name: 'Dark episodes' }))
    expect(list.getByText('Downloading 25%')).toBeInTheDocument()
    expect(list.getByRole('progressbar', { name: 'S01E04 · Ep 4 download' })).toHaveAttribute('aria-valuenow', '25')
    expect(list.getByText('No release found yet')).toBeInTheDocument()
    fireEvent.click(list.getByRole('button', { name: 'Look again now' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/collection/items/12/retry' && c.method === 'POST')).toBe(true))
  })

  it('turns automatic downloads on and off, changes the quality and stops keeping a title', async () => {
    const calls = serve({ titles: [DARK], settings: { ...SETTINGS, on: false } })
    withRouter(<CollectionPage />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Download automatically' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/collection/settings')?.body).toEqual({ on: true }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Quality' }), { target: { value: '2160' } })
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/collection/settings').pop()?.body).toEqual({ quality: 2160 }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop keeping' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/collection/titles/1' && c.method === 'DELETE')).toBe(true))
  })

  it('offers to set up the streaming server when something is kept and the server is not running', async () => {
    serve({ titles: [DARK], server: { up: false, ready: false, installed: false } })
    withRouter(<CollectionPage />)
    expect(await screen.findByText(/Downloads need Stremio's streaming server/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Set it up' })).toBeInTheDocument()
  })
})

describe('KeepButton', () => {
  it('keeps a movie in one press and then shows it is in the Collection', async () => {
    const calls = serve({})
    withRouter(<KeepButton kind="movie" imdb="tt0133093" title="The Matrix" year={1999} poster="https://img/p.jpg" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Keep this' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/collection/titles')?.body).toEqual({ kind: 'movie', imdb: 'tt0133093', title: 'The Matrix', year: 1999, poster: 'https://img/p.jpg', scope: 'all' }))
  })

  it('asks a show for all episodes or only new ones', async () => {
    const calls = serve({})
    withRouter(<KeepButton kind="series" imdb="tt5753856" title="Dark" year={2017} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Keep this' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /New episodes only/ }))
    await waitFor(() => expect((calls.find((c) => c.url === '/api/collection/titles')?.body as { scope: string }).scope).toBe('new'))
  })

  it('shows a kept title as such and stops keeping it on a second press', async () => {
    const calls = serve({ titles: [DARK] })
    withRouter(<KeepButton kind="series" imdb="tt5753856" title="Dark" />)
    fireEvent.click(await screen.findByRole('button', { name: 'In your Collection' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/collection/titles/1' && c.method === 'DELETE')).toBe(true))
  })

  it('is not shown for a title without an IMDb number', async () => {
    serve({})
    const { container } = withRouter(<KeepButton kind="movie" imdb="kitsu:1" title="X" />) as unknown as { container: HTMLElement }
    await new Promise((r) => setTimeout(r, 20))
    expect(container.querySelector('button')).toBeNull()
  })
})
