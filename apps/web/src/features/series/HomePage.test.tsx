import { act, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { SfwHome } from './HomePage'
import { useSearch } from '@/app/search'
import { useUi } from '@/app/ui'
import { useHomeTab, useScreenView } from './view'

// There is no Discover page any more: "See all" on the Popular movies row opens the whole list in a pop-up over Home.
const browse = () => screen.getByRole('dialog', { name: 'Browse' })
const toDiscover = async () => { const row = await screen.findByRole('region', { name: 'Popular movies' }); fireEvent.click(within(row).getByRole('button', { name: /See all/ })) }

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => { vi.unstubAllGlobals(); useHomeTab.setState({ tab: 'home' }); useScreenView.setState({ view: 'all' }); useSearch.setState({ q: '' }) })
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const found = (n: number, title: string, library: unknown = null) => ({ imdb: `tt000000${n}`, kind: 'movie', title, year: 2000 + n, poster: '', background: '', rating: '7.5', overview: 'A story.', genres: ['Drama'], library })
const HOME = {
  continue: [{ cid: '-9000000000001', mid: 7, title: 'Dark', sub: 'S1 · E3 · Secrets', image: '', pct: 40, left: 25 }],
  airing: [{ id: 2, title: 'Dark', poster: '', season: 2, episode: 1, name: 'Awakening', date: new Date(Date.now() + 86_400_000).toISOString().slice(0, 10) }],
  recent: [{ id: 1, kind: 'movie', title: 'The Matrix', year: 1999, poster: '' }],
  billboard: [{ id: 1, kind: 'movie', title: 'The Matrix', year: 1999, overview: 'Neo.', backdrop: '/b.jpg', poster: '', genres: ['Action'], rating: 8.2 }],
}

function mount(over: (u: string) => Response | undefined = () => undefined) {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url)
    const o = over(url); if (o) return o
    if (url.startsWith('/api/screen/home')) return json(HOME)
    if (url.startsWith('/api/screen/discover/')) return json({ ...found(1, 'The Matrix', { kind: 'movie', id: 1 }), runtime: '136 min', cast: ['Keanu Reeves'], director: ['Lana Wachowski'], trailer: 'm8e-FF8MsqU', seasons: 0 })
    if (url.startsWith('/api/screen/discover')) return json({ ok: true, items: [found(1, 'The Matrix', { kind: 'movie', id: 1 }), found(2, 'Dune')], more: false })
    return json({ items: [] })
  }))
  withRouter(<SfwHome />)
  return urls
}

describe('SFW Home', () => {
  const CARD = { cid: '', mid: 0, title: 'The Matrix', sub: '1999', image: '', pct: 60, left: 7, resume: true, addon: { kind: 'movie', ident: 'tt0133093', season: null, episode: null } }
  const withCard = (resume: Response) => mount((url) => url.startsWith('/api/screen/home') ? json({ ...HOME, continue: [CARD] }) : url === '/api/screen/stream-resume' ? resume : undefined)

  it('starts a remembered add-on title again at once, in full screen, instead of opening its title page', async () => {
    const request = vi.fn(async () => undefined)
    document.documentElement.requestFullscreen = request as never
    const urls = withCard(json({ play: '/api/screen/stream/abcdefgh1234', name: 'The Matrix', position: 600 }))
    fireEvent.click(await screen.findByRole('link', { name: 'Continue The Matrix' }))
    expect(request).toHaveBeenCalledTimes(1)                                                        // asked for in the click itself
    expect(await screen.findByText('Add-on player page')).toBeInTheDocument()
    expect(urls).toContain('/api/screen/stream-resume')
  })
  it('falls back to the title page, out of full screen, when the stream cannot be started again', async () => {
    document.documentElement.requestFullscreen = vi.fn(async () => undefined) as never
    const exit = vi.fn(async () => undefined)
    document.exitFullscreen = exit as never
    Object.defineProperty(document, 'fullscreenElement', { value: document.documentElement, configurable: true })
    withCard(json({ detail: 'Stremio\'s streaming server is not running.' }, 409))
    fireEvent.click(await screen.findByRole('link', { name: 'Continue The Matrix' }))
    await vi.waitFor(() => expect(exit).toHaveBeenCalled())
    expect(screen.queryByText('Add-on player page')).toBeNull()
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true })
  })
  it('leaves a card that has nothing remembered, or a click meant for a new tab, as a plain link', async () => {
    const request = vi.fn(async () => undefined)
    document.documentElement.requestFullscreen = request as never
    withCard(json({}))
    const link = await screen.findByRole('link', { name: 'Continue The Matrix' })
    fireEvent.click(link, { ctrlKey: true })
    expect(request).not.toHaveBeenCalled()
  })
  it('lists what was watched through an add-on too, and opens its title page (an episode, or the movie)', async () => {
    mount((url) => url.startsWith('/api/screen/home') ? json({ ...HOME, continue: [
      ...HOME.continue,
      { cid: '', mid: 0, title: 'Breaking Bad', sub: 'S1 · E2', image: '', pct: 10, left: 50, addon: { kind: 'series', ident: 'tt0903747', season: 1, episode: 2 } },
      { cid: '', mid: 0, title: 'The Matrix', sub: '1999', image: '', pct: 60, left: 7, addon: { kind: 'movie', ident: 'tt0133093', season: null, episode: null } },
    ] }) : undefined)
    expect(await screen.findByRole('link', { name: 'Continue Dark' })).toHaveAttribute('href', '/watch/-9000000000001/7')
    expect(screen.getByRole('link', { name: 'Continue Breaking Bad' })).toHaveAttribute('href', '/discover/series/tt0903747/1/2')
    expect(screen.getByRole('link', { name: 'Continue The Matrix' })).toHaveAttribute('href', '/discover/movie/tt0133093')
  })

  it('keeps a part of the page after it is left: coming back shows it at once, asks for nothing, and replays no entrance', async () => {
    const urls = mount()
    const popular = await screen.findByRole('region', { name: 'Popular movies' })
    const asked = () => urls.filter((u) => u.startsWith('/api/screen/discover?')).length
    const before = asked()
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    expect(await screen.findByRole('region', { name: 'Your library' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Popular movies' })).not.toBeInTheDocument()   // hidden, not gone
    fireEvent.click(screen.getByRole('tab', { name: 'For you' }))
    expect(screen.getByRole('region', { name: 'Popular movies' })).toBe(popular)                // the very same row: nothing was rebuilt
    expect(asked()).toBe(before)
    expect(screen.queryByRole('region', { name: 'Your library' })).not.toBeInTheDocument()
  })

  it('prepares the Library out of sight once For you has settled, so the first switch to it is instant', async () => {
    mount()
    await screen.findByRole('link', { name: 'Continue Dark' })
    expect(screen.queryByRole('region', { name: 'Your library' })).not.toBeInTheDocument()
    await vi.waitFor(() => expect(screen.getAllByRole('region', { name: 'Your library', hidden: true }).length).toBeGreaterThan(0), { timeout: 4000 })
    expect(screen.queryByRole('region', { name: 'Your library' })).not.toBeInTheDocument()      // prepared, but not shown
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    expect(screen.getByRole('region', { name: 'Your library' })).toBeInTheDocument()            // there at once
  })

  it('has one tab bar, at the same place whichever part is open, and one page heading', async () => {
    mount()
    await screen.findByRole('link', { name: 'Continue Dark' })
    expect(screen.getAllByRole('tablist', { name: 'Home' })).toHaveLength(1)                     // over the hero on For you
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    expect(screen.getAllByRole('tablist', { name: 'Home' })).toHaveLength(1)                     // under the bar on the others
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })

  it('shows what to continue, what airs this week, what was added and the featured title, from your library', async () => {
    mount()
    expect(await screen.findByRole('link', { name: 'Continue Dark' })).toHaveAttribute('href', '/watch/-9000000000001/7')
    const airing = screen.getByRole('region', { name: 'Airing this week' })
    expect(within(airing).getByText('Tomorrow')).toBeInTheDocument()
    expect(within(airing).getByText('S2 · E1')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Recently added' })).getByRole('link', { name: 'Open The Matrix' })).toHaveAttribute('href', '/movies/1')
    expect(within(screen.getByRole('region', { name: 'Featured' })).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/movies/1')
  })

  it('Browse marks what you have, opens the details of a title, and finds more by genre and by search', async () => {
    const urls = mount()
    await toDiscover()
    const list = await screen.findByRole('list', { name: 'Catalogue' })
    expect(within(list).getByText('In your library')).toBeInTheDocument()
    expect(within(list).getByRole('link', { name: 'Open Dune' })).toHaveAttribute('href', '/discover/movie/tt0000002')   // a poster opens the title's page, no pop-up
    expect(within(browse()).getByRole('heading', { name: 'Popular movies' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))                                     // one Filters button, like the Photos page
    const genres = screen.getByRole('group', { name: 'Genre' })
    for (const g of ['All genres', 'Biography', 'History', 'Sport']) expect(within(genres).getByRole('button', { name: g })).toBeInTheDocument()
    fireEvent.click(within(genres).getByRole('button', { name: 'Drama' }))
    expect(await within(browse()).findByRole('heading', { name: 'Drama movies' })).toBeInTheDocument()            // the page says which genre is on
    await vi.waitFor(() => expect(urls.some((u) => u.includes('genre=Drama'))).toBe(true))
    fireEvent.click(within(genres).getByRole('button', { name: 'Comedy' }))                              // genres add up: both are asked for
    expect(await within(browse()).findByRole('heading', { name: 'Drama · Comedy movies' })).toBeInTheDocument()
    await vi.waitFor(() => expect(urls.some((u) => u.includes('genre=Drama%2CComedy'))).toBe(true))
    fireEvent.click(within(screen.getByRole('group', { name: 'Kind' })).getByRole('button', { name: 'Series' }))
    expect(screen.getByRole('button', { name: /Filters/ })).toHaveTextContent('3')                           // the count of what is chosen: series, Drama and Comedy
    await vi.waitFor(() => expect(urls.some((u) => u.includes('type=series'))).toBe(true))
    act(() => useSearch.getState().set('dune'))                                                            // typed in the one search box, in the top bar
    await vi.waitFor(() => expect(urls.some((u) => u.includes('search=dune'))).toBe(true))
    expect(screen.queryByRole('region', { name: 'Featured' })).toBeNull()                                  // a search shows only its results, on Home itself
  })

  it('says so when Cinemeta is switched off, instead of an empty page', async () => {
    mount((u) => (u.startsWith('/api/screen/discover') && !u.includes('/discover/') ? json({ ok: true, disabled: true, items: [] }) : undefined))
    await screen.findByRole('link', { name: 'Continue Dark' })
    act(() => useSearch.getState().set('dune'))
    expect(await screen.findByText(/it is switched off/)).toBeInTheDocument()
  })

  it('on a wide window the details stay beside the posters and follow the title you pick, with no pop-up', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    mount()
    await toDiscover()
    const list = await screen.findByRole('list', { name: 'Catalogue' })
    const panel = await screen.findByRole('complementary', { name: 'Details' })
    expect(within(panel).getByText('The Matrix')).toBeInTheDocument()                                  // the first title is shown at once
    fireEvent.focus(within(list).getByRole('link', { name: 'Open Dune' }))
    expect(await within(await screen.findByRole('complementary', { name: 'Details' })).findByText('Dune')).toBeInTheDocument()
    expect(document.querySelectorAll('dialog[open]')).toHaveLength(1)                                   // only Browse itself: the details are beside the posters
  })

  it('the featured picture has a dot for each title and the active one fills as time runs out', async () => {
    mount((u) => (u.startsWith('/api/screen/home') ? json({ ...HOME, billboard: [HOME.billboard[0], { ...HOME.billboard[0], id: 2, title: 'Dune' }] }) : undefined))
    const feat = await screen.findByRole('region', { name: 'Featured' })
    expect(feat.querySelectorAll('img').length).toBe(2)
    expect(feat.querySelectorAll('.mo-dot').length).toBe(2)
    expect(feat.querySelectorAll('.mo-dot-fill').length).toBe(1)
  })

  it('has For you, Library and Collection under one heading and no Discover page: See all opens the list in a pop-up, the Library part lists your series and movies, and a search is answered on Home', async () => {
    const urls = mount()
    expect(await screen.findByRole('region', { name: 'Featured' })).toBeInTheDocument()                    // For you is where it opens
    expect(screen.queryByRole('tab', { name: 'Discover' })).toBeNull()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['For you', 'Library', 'Collection'])
    const row = await screen.findByRole('region', { name: 'Popular movies' })
    fireEvent.click(within(row).getByRole('button', { name: 'See all →' }))
    expect(await within(browse()).findByRole('list', { name: 'Catalogue' })).toBeInTheDocument()           // "See all" opens the whole list over Home
    fireEvent.click(within(browse()).getByRole('button', { name: 'Close' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: 'Browse' })).toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Series' }))
    await vi.waitFor(() => expect(urls.some((u) => u.startsWith('/api/screen/series'))).toBe(true))
    fireEvent.click(screen.getByRole('tab', { name: 'For you' }))
    act(() => useSearch.getState().set('dune'))                                                            // typed in the one search box, in the top bar
    expect(await screen.findByRole('list', { name: 'Catalogue' })).toBeInTheDocument()                     // the results are on Home, under its own tabs
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    await vi.waitFor(() => expect(urls.some((u) => u.includes('search=dune'))).toBe(true))
    act(() => useSearch.getState().set(''))
    expect(await screen.findByRole('region', { name: 'Popular movies' })).toBeInTheDocument()              // clearing the search is back to the rows
  })

  it('lets the featured picture run under the top bar while it is on screen, and gives the bar back otherwise', async () => {
    mount()
    await screen.findByRole('region', { name: 'Featured' })
    expect(useUi.getState().immersive).toBe(true)
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    await vi.waitFor(() => expect(useUi.getState().immersive).toBe(false))
  })

  it('Filters also picks the list (Popular, New, Featured and the streaming services), says when a service list needs the TMDB key, and pages by what the server says is next', async () => {
    const urls = mount((u) => (u.includes('catalog=hbomax') ? json({ ok: true, needs_key: true, items: [] }) : u.includes('catalog=netflix&') || u.endsWith('catalog=netflix') ? json({ ok: true, items: [found(1, 'The Matrix')], more: true, next: 1 }) : undefined))
    await toDiscover()
    fireEvent.click(await screen.findByRole('button', { name: /Filters/ }))
    const lists = screen.getByRole('group', { name: 'List' })
    for (const l of ['Popular', 'New', 'Featured', 'Netflix', 'HBO Max', 'Disney+', 'Prime Video', 'Apple TV+', 'Anime airing now', 'Top anime series', 'Top anime movies']) expect(within(lists).getByRole('button', { name: l })).toBeInTheDocument()
    fireEvent.click(within(lists).getByRole('button', { name: 'Netflix' }))
    expect(await within(browse()).findByRole('heading', { name: 'Netflix movies' })).toBeInTheDocument()
    await vi.waitFor(() => expect(urls.some((u) => u.includes('catalog=netflix'))).toBe(true))
    fireEvent.click(await screen.findByRole('button', { name: 'Show more' }))
    await vi.waitFor(() => expect(urls.some((u) => u.includes('catalog=netflix') && u.includes('skip=1'))).toBe(true))          // TMDB pages by number, not by fifty
    fireEvent.click(within(lists).getByRole('button', { name: 'HBO Max' }))
    expect(await screen.findByText(/comes from TMDB/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Filters/ })).toHaveTextContent('1')
  })

  it('shows the anime lists from their own numbers: Kind does not apply, and a title opens its own page', async () => {
    const anime = { imdb: 'kitsu:12', kind: 'tv', title: 'One Piece', year: 1999, poster: '', background: '', rating: '', overview: 'Pirates.', genres: ['Action'], library: null }
    mount((u) => (u.includes('catalog=anime-series') ? json({ ok: true, items: [anime], more: false }) : undefined))
    await toDiscover()
    fireEvent.click(await screen.findByRole('button', { name: /Filters/ }))
    fireEvent.click(within(screen.getByRole('group', { name: 'List' })).getByRole('button', { name: 'Top anime series' }))
    expect(await within(browse()).findByRole('heading', { name: 'Top anime series' })).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Kind' })).getByRole('button', { name: 'Movies' })).toBeDisabled()
    expect(await within(browse()).findByRole('link', { name: 'Open One Piece' })).toHaveAttribute('href', expect.stringContaining('/discover/series/kitsu'))   // an anime opens its own page, with its episodes
  })

  it('puts the lists of the add-ons you added in Filters, and asks the server for the one you pick', async () => {
    const addon = { id: 'abcdef0123', url: 'https://good.example', name: 'My Catalogs', description: '', version: '', lists: [{ type: 'movie', id: 'top', name: 'Top films' }] }
    const urls = mount((u) => (u === '/api/screen/addons' ? json({ builtin: [], items: [addon] }) : u.includes('catalog=addon') ? json({ ok: true, items: [found(5, 'From the add-on')], more: false }) : undefined))
    await toDiscover()
    fireEvent.click(await screen.findByRole('button', { name: /Filters/ }))
    fireEvent.click(await within(await screen.findByRole('group', { name: 'Your add-ons' })).findByRole('button', { name: 'My Catalogs: Top films (movies)' }))
    expect(await within(browse()).findByRole('heading', { name: 'My Catalogs: Top films (movies)' })).toBeInTheDocument()
    await vi.waitFor(() => expect(urls.some((u) => u.includes('catalog=addon%3Aabcdef0123%3Amovie%3Atop'))).toBe(true))
    expect(within(screen.getByRole('group', { name: 'Kind' })).getByRole('button', { name: 'Series' })).toBeDisabled()    // the add-on's list has its own kind
  })

  it('leaves the lists of a removed built-in add-on out of Filters', async () => {
    mount((u) => (u === '/api/screen/addons' ? json({ builtin: [{ id: 'cinemeta', name: 'Cinemeta', what: '' }], removed: [{ id: 'streaming', name: 'Streaming Catalogs', what: '' }, { id: 'anime', name: 'Anime Catalogs', what: '' }], items: [] }) : undefined))
    await toDiscover()
    fireEvent.click(await screen.findByRole('button', { name: /Filters/ }))
    const names = async () => within(await screen.findByRole('group', { name: 'List' })).getAllByRole('button').map((b) => b.textContent)
    await vi.waitFor(async () => expect(await names()).toEqual(['Popular', 'New', 'Featured']))
  })

  it('moves to a list that is still offered when the one that was open is removed', async () => {
    mount((u) => (u === '/api/screen/addons' ? json({ builtin: [{ id: 'streaming', name: 'Streaming Catalogs', what: '' }], removed: [{ id: 'cinemeta', name: 'Cinemeta', what: '' }, { id: 'anime', name: 'Anime Catalogs', what: '' }], items: [] }) : undefined))
    await toDiscover()
    fireEvent.click(await screen.findByRole('button', { name: /Filters/ }))
    const group = await screen.findByRole('group', { name: 'List' })
    await vi.waitFor(() => expect(within(group).getByRole('button', { name: 'Netflix' })).toHaveAttribute('aria-pressed', 'true'))   // Popular is gone, so the first list left is open
    expect(within(group).queryByRole('button', { name: 'Popular' })).not.toBeInTheDocument()
  })

  it('keeps Where to watch off the Discover preview: it is on the title page (the title you own does not need it)', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    const urls = mount((u) => (u.includes('/discover/movie/tt0000002') ? json({ ...found(2, 'Dune'), runtime: '155 min', cast: [], director: [], trailer: '', seasons: 0 }) : undefined))
    await toDiscover()
    const list = await screen.findByRole('list', { name: 'Catalogue' })
    fireEvent.focus(within(list).getByRole('link', { name: 'Open Dune' }))
    expect(await within(await screen.findByRole('complementary', { name: 'Details' })).findByText('Dune')).toBeInTheDocument()
    expect(screen.queryByText('Where to watch')).toBeNull()
    expect(urls.some((u) => u.startsWith('/api/screen/where/'))).toBe(false)
    fireEvent.focus(within(list).getByRole('link', { name: 'Open The Matrix' }))      // in the library
    await within(await screen.findByRole('complementary', { name: 'Details' })).findByText('The Matrix')
  })

  it('has no "Open the page" or "Open in your library" button in the side panel: the poster itself opens the title', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    mount((u) => (u.includes('/discover/movie/tt0000002') ? json({ ...found(2, 'Dune'), runtime: '155 min', cast: [], director: [], trailer: '', seasons: 0 }) : undefined))
    await toDiscover()
    const list = await screen.findByRole('list', { name: 'Catalogue' })
    for (const title of ['The Matrix', 'Dune']) {                                      // one in the library, one not
      fireEvent.focus(within(list).getByRole('link', { name: `Open ${title}` }))
      const panel = await screen.findByRole('complementary', { name: 'Details' })
      await within(panel).findByRole('heading', { name: title })
      expect(within(panel).queryByRole('link', { name: 'Open the page' })).toBeNull()
      expect(within(panel).queryByRole('link', { name: 'Open in your library' })).toBeNull()
      expect(within(panel).queryByRole('button', { name: /^Open/ })).toBeNull()
    }
    expect(within(await screen.findByRole('complementary', { name: 'Details' })).getByText('Not in your library')).toBeInTheDocument()   // Dune
    expect(within(list).getByRole('link', { name: 'Open Dune' })).toHaveAttribute('href', expect.stringContaining('/discover/movie/'))   // the way in is the poster
  })
})
