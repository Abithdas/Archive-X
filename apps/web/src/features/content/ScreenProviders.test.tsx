import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ScreenProviders } from './ScreenProviders'
import { candidateText, statusText, typedKind } from './screen'
import { withRouter } from '@/test/withRouter'

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })

function mount(mode: 'sfw' | 'nsfw', extra: Record<string, unknown> = {}) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url === '/api/content-mode') return json({ ok: true, mode, pin_set: true, locked_for: 0 })
    if (url === '/api/screen/settings' && !init?.method) return json({ tmdb_api_key: 'abcd…5678', tmdb_configured: true, omdb_api_key: '', omdb_configured: false, opensubtitles_api_key: '', opensubtitles_configured: false })
    if (url === '/api/screen/status') return json({ progress: {}, counts: { MATCHED: 4, REVIEW: 1 }, pending: 2, configured: true })
    if (url === '/api/screen/review') return json({ items: extra.items ?? [] })
    return json({ ok: true })
  }))
  withRouter(<ScreenProviders />)
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('ScreenProviders', () => {
  it('is hidden in NSFW mode', async () => {
    const calls = mount('nsfw')
    await waitFor(() => expect(calls.some((c) => c.url === '/api/content-mode')).toBe(true))
    expect(screen.queryByLabelText('Movies and TV providers')).toBeNull()
  })

  it('shows the saved key, the counts, and saves a new key', async () => {
    const calls = mount('sfw')
    const tmdb = await screen.findByLabelText(/TMDB/)
    await waitFor(() => expect(tmdb).toHaveAttribute('placeholder', 'Saved abcd…5678 — leave blank to keep'))
    expect(await screen.findByText('4 identified · 1 to check · 2 waiting')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/OMDb/), { target: { value: 'omdbkey123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save keys' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/screen/settings' && c.method === 'POST')?.body).toEqual({ omdb_api_key: 'omdbkey123' }))
  })

  it('lets the person pick the right title for a doubtful match', async () => {
    const items = [{ id: 7, filename: 'Some Film (2019).mp4', title: 'x', state: 'REVIEW', season: null, episode: null, candidates: [{ kind: 'movie', tmdb_id: 603, title: 'The Matrix', year: 1999, score: 0.7, source: 'tmdb' }], parsed: { kind: 'movie', title: 'Some Film', year: 2019, season: null, episode: null } }]
    const calls = mount('sfw', { items })
    fireEvent.click(await screen.findByRole('button', { name: 'The Matrix (1999) · Movie' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/screen/review/7')?.body).toEqual({ kind: 'movie', tmdb_id: 603, season: null, episode: null }))
  })
})

describe('settling a video by hand', () => {
  const item = { id: 7, filename: 'blurry.mkv', title: 'x', state: 'UNMATCHED', season: null, episode: null, candidates: [], parsed: { kind: 'movie', title: 'blurry', year: null, season: null, episode: null } }
  it('searches by words and uses the title that is picked', async () => {
    const calls = mount('sfw', { items: [item] })
    fireEvent.change(await screen.findByLabelText('Find a title for blurry.mkv'), { target: { value: 'the matrix' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(calls.some((c) => c.url.startsWith('/api/screen/search?q=the%20matrix&kind=movie'))).toBe(true))
  })
  it('uses a pasted IMDb or TMDB id without searching', async () => {
    const calls = mount('sfw', { items: [item] })
    const box = await screen.findByLabelText('Find a title for blurry.mkv')
    fireEvent.change(box, { target: { value: 'tt0133093' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this IMDb id' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/screen/review/7')?.body).toEqual({ kind: 'movie', imdb_id: 'tt0133093', season: null, episode: null }))
    fireEvent.change(box, { target: { value: '603' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this TMDB id' }))
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/screen/review/7')).toHaveLength(2))
    expect(calls.filter((c) => c.url.startsWith('/api/screen/search'))).toHaveLength(0)
  })
})

describe('testing a key', () => {
  it('works on a key that was just typed, saving it first, and shows the provider\'s own reason', async () => {
    const calls: { url: string; method: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url === '/api/content-mode') return json({ ok: true, mode: 'sfw', pin_set: true, locked_for: 0 })
      if (url === '/api/screen/settings' && !init?.method) return json({ tmdb_api_key: '', tmdb_configured: false, omdb_api_key: '', omdb_configured: false, opensubtitles_api_key: '', opensubtitles_configured: false })
      if (url === '/api/screen/status') return json({ progress: {}, counts: {}, pending: 0, configured: false })
      if (url === '/api/screen/review') return json({ items: [] })
      if (url === '/api/screen/check') return json({ ok: false, error: 'OMDb does not recognise this key (it said: Invalid API key). OMDb emails you an activation link when you ask for a key: open it, then test again.' })
      return json({ ok: true })
    }))
    withRouter(<ScreenProviders />)
    const test = (await screen.findAllByRole('button', { name: 'Test' }))
    expect(test.every((b) => (b as HTMLButtonElement).disabled)).toBe(true)                      // nothing typed, nothing saved: nothing to test
    fireEvent.change(await screen.findByLabelText(/OMDb/), { target: { value: 'abc12345' } })
    const omdb = screen.getAllByRole('button', { name: 'Test' })[2] as HTMLButtonElement
    await waitFor(() => expect(omdb.disabled).toBe(false))
    fireEvent.click(omdb)
    expect(await screen.findByText(/emails you an activation link/)).toBeInTheDocument()
    expect(calls.findIndex((c) => c.url === '/api/screen/settings' && c.method === 'POST')).toBeLessThan(calls.findIndex((c) => c.url === '/api/screen/check'))   // saved, then tested
    expect(calls.find((c) => c.url === '/api/screen/settings' && c.method === 'POST')?.body).toEqual({ omdb_api_key: 'abc12345' })
  })
})

describe('TVmaze switch', () => {
  it('is saved when switched', async () => {
    const calls = mount('sfw')
    fireEvent.click(await screen.findByRole('switch', { name: 'Use TVmaze for TV shows' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/screen/settings' && c.method === 'POST')?.body).toEqual({ tvmaze_enabled: true }))
  })
})

describe('words', () => {
  it('describes progress and candidates', () => {
    expect(statusText({ progress: { running: true, total: 9, done: 3 }, counts: {}, pending: 0, configured: true })).toBe('Identifying: 3 of 9')
    expect(statusText({ progress: {}, counts: {}, pending: 0, configured: false })).toMatch(/Add a TMDB key/)
    expect(['tt0133093', ' 603 ', 'the matrix', ''].map(typedKind)).toEqual(['imdb', 'tmdb', 'words', 'none'])
    expect(candidateText({ kind: 'tv', tmdb_id: 1, title: 'Dark', year: 2017, score: 1, source: 'tmdb' })).toBe('Dark (2017) · TV show')
  })
})
