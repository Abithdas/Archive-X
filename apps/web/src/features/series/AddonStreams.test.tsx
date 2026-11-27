import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AddonExtras, AddonStreams, readStreamPrefs, sizeText } from './AddonStreams'

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
const DATA = { ok: true, skipped: 2, addons: ['Home server', 'Links'], items: [
  { addon: 'Links', name: 'Trailer', kind: 'link', href: 'https://www.youtube.com/watch?v=abcdefghijk' },
  { addon: 'Home server', name: 'Dune 1080p', kind: 'play', play: '/api/screen/stream/tok123' },
  { addon: 'Home server', name: 'Elsewhere', kind: 'blocked', host: 'cdn.example' },
] }

describe('streams from the add-ons on a title page', () => {
  it('opens links elsewhere, plays what is allowed here, and says what is not played', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); return json(DATA) }))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const home = await screen.findByRole('list', { name: 'Home server' })
    expect(urls[0]).toBe('/api/screen/streams/movie/tt1160419')
    expect(screen.getByRole('link', { name: /Trailer/ })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=abcdefghijk')
    expect(screen.getByRole('link', { name: /Trailer/ })).toHaveAttribute('target', '_blank')
    expect(within(home).getByText('Not played here (cdn.example)')).toBeInTheDocument()
    expect(screen.getByText('2 streams of a kind this app does not use were left out.')).toBeInTheDocument()
    fireEvent.click(within(home).getByRole('button', { name: /Dune 1080p/ }))
    expect(await screen.findByText('Add-on player page')).toBeInTheDocument()   // a page of its own with the app's player, not a small box in the panel
    expect(document.querySelector('dialog[open]')).toBeNull()
  })
  it('opens the whole row, not only its title', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(DATA)))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const home = await screen.findByRole('list', { name: 'Home server' })
    fireEvent.click(within(home).getByText('Play'))
    expect(await screen.findByText('Add-on player page')).toBeInTheDocument()
  })
  it('asks with the season and episode for a show, and shows nothing when no add-on answers', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); return json({ ok: true, items: [], skipped: 0, addons: [] }) }))
    const { container } = withRouter(<AddonStreams kind="series" ident="tt0903747" season={1} episode={2} />)
    await vi.waitFor(() => expect(urls).toEqual(['/api/screen/streams/series/tt0903747?season=1&episode=2']))
    expect(container.querySelector('[aria-label="From your add-ons"]')).toBeNull()
  })
})

const TORRENTS = { ok: true, skipped: 0, addons: ['Torrentio'], torrents: true, streaming_server: { url: 'http://127.0.0.1:11470', up: true }, items: [
  { addon: 'Torrentio', name: 'Torrentio 1080p · A', kind: 'play', play: '/api/screen/stream/a', torrent: true, seeders: 5, size: 3 * 1024 ** 3, provider: 'IndexA', height: 1080 },
  { addon: 'Torrentio', name: 'Torrentio 4k · B', kind: 'play', play: '/api/screen/stream/b', torrent: true, seeders: 90, size: 12 * 1024 ** 3, provider: 'IndexB', height: 2160, cached: true },
  { addon: 'Torrentio', name: 'Torrentio 720p · C', kind: 'play', play: '/api/screen/stream/c', torrent: true, seeders: 40, size: 1.2 * 1024 ** 3, provider: 'IndexC', height: 720 },
] }
const order = () => within(screen.getByRole('list', { name: 'Torrentio' })).getAllByRole('button').map((b) => b.textContent?.slice(0, 22))

describe('torrent streams', () => {
  beforeEach(() => { localStorage.clear() })
  it('shows seeds, size and where from, and sorts by the seeds and sizes the server read from the add-on text', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(TORRENTS)))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    expect(within(list).getByText(/IndexB · 90 seeds · 12.0 GB/)).toBeInTheDocument()
    expect(order()).toEqual(['Torrentio 1080p · A', 'Torrentio 4k · B', 'Torrentio 720p · C'])
    fireEvent.change(screen.getByLabelText('Addon order'), { target: { value: 'seeds' } })
    expect(order()).toEqual(['Torrentio 4k · B', 'Torrentio 720p · C', 'Torrentio 1080p · A'])
    fireEvent.change(screen.getByLabelText('Addon order'), { target: { value: 'smallest' } })
    expect(order()).toEqual(['Torrentio 720p · C', 'Torrentio 1080p · A', 'Torrentio 4k · B'])
  })
  it('has an "Instant only" filter when a debrid service already has some, and marks those', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(TORRENTS)))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    expect(within(list).getByText('Instant')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Instant only' }))
    expect(order()).toEqual(['Torrentio 4k · B'])
    expect(screen.getByText('1 stream')).toBeInTheDocument()
  })
  it('does not offer "Instant only" when nothing is cached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...TORRENTS, items: TORRENTS.items.map((i) => ({ ...i, cached: undefined })) })))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    await screen.findByRole('list', { name: 'Torrentio' })
    expect(screen.queryByRole('button', { name: 'Instant only' })).toBeNull()
  })
  it('remembers how streams were sorted and filtered for the next title, but not the quality or the add-on', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(TORRENTS)))
    const { unmount } = withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    await screen.findByRole('list', { name: 'Torrentio' })
    fireEvent.change(screen.getByLabelText('Addon order'), { target: { value: 'seeds' } })
    fireEvent.click(screen.getByRole('button', { name: 'Instant only' }))
    fireEvent.change(screen.getByLabelText('Quality'), { target: { value: '2160' } })
    expect(readStreamPrefs()).toEqual({ sort: 'seeds', language: '', source: '', hdr: 'all', instant: true })
    unmount()
    withRouter(<AddonStreams kind="movie" ident="tt0133093" />)
    await screen.findByRole('list', { name: 'Torrentio' })
    expect(screen.getByLabelText('Addon order')).toHaveValue('seeds')
    expect(screen.getByRole('button', { name: 'Instant only' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Quality')).toHaveValue('')
  })
  it('lists a torrent that cannot play yet, and says the streaming server is missing, with a link to set it up', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...TORRENTS, streaming_server: { url: 'http://127.0.0.1:11470', up: false }, items: [{ addon: 'Torrentio', name: 'Torrentio 1080p · A', kind: 'blocked', host: 'streaming server', torrent: true, seeders: 5 }] })))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    expect(within(list).getByText(/it needs Stremio's streaming server, which is not running at http:\/\/127.0.0.1:11470/)).toBeInTheDocument()
    expect(within(list).getByRole('link', { name: 'Set it up' })).toHaveAttribute('href', '/settings')
    expect(within(list).queryByRole('button')).toBeNull()                                    // nothing to click: it cannot play
    fireEvent.click(within(list).getByRole('link', { name: 'Set it up' }))
    expect(localStorage.getItem('ax_next_settings_panel')).toBe('addons')
  })
  it('ignores broken saved choices', () => {
    localStorage.setItem('archive-x:streams-prefs', '{"sort":"nonsense","hdr":5,"language":{"a":1},"instant":"yes"}')
    expect(readStreamPrefs()).toEqual({ sort: 'addon', language: '', source: '', hdr: 'all', instant: false })
    localStorage.setItem('archive-x:streams-prefs', 'not json')
    expect(readStreamPrefs().sort).toBe('addon')
  })
  it('writes sizes the way people read them', () => {
    expect([sizeText(0), sizeText(500), sizeText(1536), sizeText(3 * 1024 ** 3), sizeText(12.3 * 1024 ** 3), sizeText(1024 ** 4 * 2)]).toEqual(['', '500 B', '2 KB', '3.0 GB', '12.3 GB', '2.0 TB'])
  })
})

describe('extra details from the add-ons', () => {
  it('shows the add-on notes and its links, and nothing when no add-on has any', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ok: true, items: [{ addon: 'Deep Dive', text: 'Production notes.', rating: '8.4', links: [{ name: 'Reviews', category: 'x', url: 'https://r.example/1' }] }] })))
    const { unmount } = withRouter(<AddonExtras kind="series" ident="tt0903747" />)
    expect(await screen.findByText('Production notes.')).toBeInTheDocument()
    expect(screen.getByText('8.4 on IMDb')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Reviews/ })).toHaveAttribute('href', 'https://r.example/1')
    unmount()
    vi.stubGlobal('fetch', vi.fn(async () => json({ ok: true, items: [] })))
    withRouter(<AddonExtras kind="series" ident="tt0903747" />)
    await Promise.resolve()
    expect(screen.queryByRole('region', { name: 'More from your add-ons' })).toBeNull()
  })
})

describe('same kind of stream on the next episode (bingeGroup)', () => {
  it('remembers the kind played and offers it first on the next episode', async () => {
    localStorage.clear()
    const eps = { ok: true, skipped: 0, addons: ['A'], items: [
      { addon: 'A', name: 'Other 1080p', kind: 'play', play: '/api/screen/stream/t1', bingeGroup: 'a-1080' },
      { addon: 'A', name: 'Mine 720p', kind: 'play', play: '/api/screen/stream/t2', bingeGroup: 'a-720' },
    ] }
    vi.stubGlobal('fetch', vi.fn(async () => json(eps)))
    const first = withRouter(<AddonStreams kind="series" ident="tt0903747" season={1} episode={1} />)
    fireEvent.click(await screen.findByRole('button', { name: /Mine 720p/ }))
    first.unmount()
    withRouter(<AddonStreams kind="series" ident="tt0903747" season={1} episode={2} />)
    const list = await screen.findByRole('list', { name: 'A' })
    const names = within(list).getAllByRole('button').map((b) => b.textContent)
    expect(names[0]).toBe('Mine 720p')                                  // the kind played last time comes first
    expect(screen.getByText('Same as last episode')).toBeInTheDocument()
  })
})

describe('torrents without the streaming server', () => {
  it('offers to start it when it is installed, and refreshes the streams once it is up', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.startsWith('/api/screen/streams/')) return new Response(JSON.stringify({ ok: true, skipped: 0, addons: ['Torrentio'], torrents: true, streaming_server: { url: 'http://127.0.0.1:11470', up: false }, items: [{ addon: 'Torrentio', name: 'Torrentio 1080p', kind: 'blocked', host: 'streaming server', torrent: true, height: 1080 }] }), { status: 200 })
      if (url === '/api/screen/streaming-server/start') return new Response(JSON.stringify({ ok: true, up: true }), { status: 200 })
      if (url === '/api/screen/streaming-server') return new Response(JSON.stringify({ url: 'http://127.0.0.1:11470', up: false, installed: true, download: 'https://example.test/get' }), { status: 200 })
      return new Response('{}', { status: 200 })
    }))
    withRouter(<AddonStreams kind="movie" ident="tt0133093" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Start it' }))
    await waitFor(() => expect(calls).toContain('POST /api/screen/streaming-server/start'))
    await waitFor(() => expect(calls.filter((c) => c.includes('/api/screen/streams/')).length).toBeGreaterThan(1))   // asked again now that it is up
  })
})
