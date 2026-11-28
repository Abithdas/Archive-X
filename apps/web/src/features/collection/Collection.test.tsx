import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

describe('KeepButton', () => {
  it('keeps a movie in one press', async () => {
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
    fireEvent.click(await screen.findByRole('button', { name: 'Kept' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/collection/titles/1' && c.method === 'DELETE')).toBe(true))
  })

  it('is not shown for a title without an IMDb number', async () => {
    serve({})
    const { container } = withRouter(<KeepButton kind="movie" imdb="kitsu:1" title="X" />) as unknown as { container: HTMLElement }
    await new Promise((r) => setTimeout(r, 20))
    expect(container.querySelector('button')).toBeNull()
  })
})
