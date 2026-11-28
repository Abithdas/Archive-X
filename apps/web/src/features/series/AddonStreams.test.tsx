import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: toastError }), Toaster: () => null }))
import { AddonExtras, AddonStreams, readStreamPrefs, sizeText, typeLine } from './AddonStreams'

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
const order = () => within(screen.getByRole('list', { name: 'Torrentio' })).getAllByRole('button').filter((b) => !/^Download /.test(b.getAttribute('aria-label') ?? '')).map((b) => b.textContent?.slice(0, 22))

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

describe('the download button on a stream', () => {
  beforeEach(() => { localStorage.clear() })
  const serveStreams = (extra: (u: string, init?: RequestInit) => Response | null = () => null) => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
      calls.push({ url: u, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      const own = extra(u, init)
      if (own) return own
      if (u.startsWith('/api/screen/streams/')) return json({ ...TORRENTS, items: [...TORRENTS.items, { addon: 'Torrentio', name: 'A direct file', kind: 'play', play: '/api/screen/stream/d', height: 1080 }] })
      if (u === '/api/collection') return json({ settings: { on: false, dir: 'C:/Videos/Archive X Collection', quality: 1080, max_gb_movie: 12, max_gb_episode: 4 }, titles: [], server: { up: true, ready: true, installed: false }, running: false })
      return json({ ok: true })
    }))
    return calls
  }
  it('is on each torrent stream and not on a direct file, and does not play the stream', async () => {
    serveStreams()
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    expect(within(list).getAllByRole('button', { name: /^Download / })).toHaveLength(3)                       // the three torrents, not the direct file
    expect(within(list).queryByRole('button', { name: 'Download A direct file' })).toBeNull()
    fireEvent.click(within(list).getByRole('button', { name: 'Download Torrentio 4k · B' }))
    expect(screen.queryByText('Add-on player page')).toBeNull()                                                // the click is the download, not the row's play
  })
  it('sends the stream with its title and opens the Downloads window', async () => {
    const calls = serveStreams()
    withRouter(<AddonStreams kind="series" ident="tt0903747" season={1} episode={2} />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    fireEvent.click(within(list).getByRole('button', { name: 'Download Torrentio 1080p · A' }))
    const dialog = await screen.findByRole('dialog', { name: 'Downloads' })
    expect(within(dialog).getByRole('tab', { name: 'Downloads (0)' })).toBeInTheDocument()
    const sent = calls.find((c) => c.url === '/api/screen/stream-download')!
    expect(sent.method).toBe('POST')
    expect(sent.body).toEqual({ play: '/api/screen/stream/a', kind: 'series', ident: 'tt0903747', season: 1, episode: 2, name: 'Torrentio 1080p · A' })
  })
  it('says why it could not start and does not open the window', async () => {
    serveStreams((u) => (u === '/api/screen/stream-download' ? new Response(JSON.stringify({ detail: 'That is already downloading.' }), { status: 409, headers: { 'content-type': 'application/json' } }) : null))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    fireEvent.click(within(list).getByRole('button', { name: 'Download Torrentio 4k · B' }))
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('That is already downloading.'))
    expect(screen.queryByRole('dialog', { name: 'Downloads' })).toBeNull()
  })
  it('has a Downloads button on the streams header that opens the window, with a count of what is there', async () => {
    serveStreams((u) => (u === '/api/collection' ? json({ settings: { on: false, dir: 'D:/Dl', quality: 1080, max_gb_movie: 12, max_gb_episode: 4 }, running: false, server: { up: true, ready: true, installed: false }, titles: [
      { id: 1, kind: 'movie', imdb: 'tt1', title: 'The End of Oak Street', year: 2026, poster: '', scope: 'all', monitored: false, manual: true, counts: { WANTED: 0, DOWNLOADING: 1, DONE: 0, SKIPPED: 0 }, items: [{ id: 9, season: 0, episode: 0, name: '', label: 'The End of Oak Street', air_date: '', state: 'DOWNLOADING', release: '1080p Web', note: '', got: 750, total: 1500, attempts: 1 }] }] }) : null))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Downloads (1)' }))
    const dialog = await screen.findByRole('dialog', { name: 'Downloads' })
    expect(await within(dialog).findByRole('progressbar', { name: 'The End of Oak Street download' })).toHaveAttribute('aria-valuenow', '50')
    expect(within(dialog).getByText('Saved to D:/Dl')).toBeInTheDocument()
  })
})

describe('how a stream row reads (like dx.bi)', () => {
  beforeEach(() => { localStorage.clear() })
  it('names the kind of release from the source and codec', () => {
    expect(typeLine({ name: 'Torrentio', source: 'WEB-DL', release: 'The.End.of.Oak.Street.2026.1080p.WEB-DL.HEVC.x265' })).toBe('Web · HEVC')
    expect(typeLine({ name: 'Torrentio', release: 'Movie 2020 1080p BluRay x264' })).toBe('BluRay')
    expect(typeLine({ name: 'Torrentio', release: 'Movie 2020 2160p REMUX AV1' })).toBe('Remux · AV1')
    expect(typeLine({ name: 'Home server 1080p' })).toBe('')                                    // nothing known: the row shows the add-on's own text
  })
  it('puts the kind first, the facts under it and the file name last, and says each thing once', async () => {
    const data = { ...TORRENTS, items: [{ addon: 'Torrentio', name: 'Torrentio · 1080p · The.End.of.Oak.Street.WEB-DL.HEVC', release: 'The.End.of.Oak.Street.WEB-DL.HEVC', kind: 'play', play: '/api/screen/stream/a', torrent: true, seeders: 1239, size: 1.5 * 1024 ** 3, provider: 'ThePirateBay', source: 'WEB-DL', languages: ['English'], height: 1080 }] }
    vi.stubGlobal('fetch', vi.fn(async (u: string) => json(u === '/api/collection' ? {} : data)))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    const list = await screen.findByRole('list', { name: 'Torrentio' })
    expect(within(list).getByRole('button', { name: 'Torrentio · 1080p · The.End.of.Oak.Street.WEB-DL.HEVC' })).toHaveTextContent('Web · HEVC')
    expect(within(list).getByText('Torrentio · ThePirateBay · English · 1239 seeds · 1.5 GB')).toBeInTheDocument()   // no "WEB-DL" again: the first line has it
    expect(within(list).getByText('The.End.of.Oak.Street.WEB-DL.HEVC')).toHaveAttribute('title', 'The.End.of.Oak.Street.WEB-DL.HEVC')
    expect(within(list).queryByText('Play')).toBeNull()
  })
  it('turns a filter white while it is set and has a Reset that clears them', async () => {
    vi.stubGlobal('fetch', vi.fn(async (u: string) => json(u === '/api/collection' ? {} : TORRENTS)))
    withRouter(<AddonStreams kind="movie" ident="tt1160419" />)
    await screen.findByRole('list', { name: 'Torrentio' })
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull()
    const quality = screen.getByLabelText('Quality')
    expect(quality.className).not.toContain('bg-white')
    fireEvent.change(quality, { target: { value: '1080' } })
    expect(screen.getByLabelText('Quality').className).toContain('bg-white')
    expect(screen.getByText('1 stream')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect(screen.getByLabelText('Quality').className).not.toContain('bg-white')
    expect(screen.getByText('3 streams')).toBeInTheDocument()
  })
})
