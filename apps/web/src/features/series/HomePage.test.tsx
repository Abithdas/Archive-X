import { act, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { SfwHome } from './HomePage'
import { useSearch } from '@/app/search'
import { useUi } from '@/app/ui'
import { useHomeTab, useScreenView } from './view'

const toDiscover = async () => fireEvent.click(await screen.findByRole('tab', { name: 'Discover' }))

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
  it('shows what to continue, what airs this week, what was added and the featured title, from your library', async () => {
    mount()
    expect(await screen.findByRole('link', { name: 'Continue Dark' })).toHaveAttribute('href', '/watch/-9000000000001/7')
    const airing = screen.getByRole('region', { name: 'Airing this week' })
    expect(within(airing).getByText('Tomorrow')).toBeInTheDocument()
    expect(within(airing).getByText('S2 · E1')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Recently added' })).getByRole('link', { name: 'Open The Matrix' })).toHaveAttribute('href', '/movies/1')
    expect(within(screen.getByRole('region', { name: 'Featured' })).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/movies/1')
  })

  it('Discover marks what you have, opens the details of a title, and finds more by genre and by search', async () => {
    const urls = mount()
    await toDiscover()
    const list = await screen.findByRole('list', { name: 'Catalogue' })
    expect(within(list).getByText('In your library')).toBeInTheDocument()
    fireEvent.click(within(list).getByRole('button', { name: 'Details of Dune' }))
    expect(await screen.findByText('Not in your library')).toBeInTheDocument()
    expect(await screen.findByText(/Keanu Reeves/)).toBeInTheDocument()                                  // the details came from the catalogue
    expect(screen.getByRole('link', { name: 'Watch the trailer' })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=m8e-FF8MsqU')
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[0]!)
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'Drama' } })
    await vi.waitFor(() => expect(urls.some((u) => u.includes('genre=Drama'))).toBe(true))
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'series' } })
    await vi.waitFor(() => expect(urls.some((u) => u.includes('type=series'))).toBe(true))
    act(() => useSearch.getState().set('dune'))                                                            // typed in the one search box, in the top bar
    await vi.waitFor(() => expect(urls.some((u) => u.includes('search=dune'))).toBe(true))
    expect(screen.queryByRole('region', { name: 'Featured' })).toBeNull()                                  // a search shows only its results
  })

  it('says so when Cinemeta is switched off, instead of an empty page', async () => {
    mount((u) => (u.startsWith('/api/screen/discover') && !u.includes('/discover/') ? json({ ok: true, disabled: true, items: [] }) : undefined))
    await toDiscover()
    expect(await screen.findByText(/it is switched off/)).toBeInTheDocument()
  })

  it('on a wide window the details stay beside the posters and follow the title you pick, with no pop-up', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    mount()
    await toDiscover()
    const list = await screen.findByRole('list', { name: 'Catalogue' })
    const panel = await screen.findByRole('complementary', { name: 'Details' })
    expect(within(panel).getByText('The Matrix')).toBeInTheDocument()                                  // the first title is shown at once
    fireEvent.click(within(list).getByRole('button', { name: 'Details of Dune' }))
    expect(await within(await screen.findByRole('complementary', { name: 'Details' })).findByText('Dune')).toBeInTheDocument()
    expect(document.querySelector('dialog[open]')).toBeNull()
  })

  it('the featured picture has a dot for each title and the active one fills as time runs out', async () => {
    mount((u) => (u.startsWith('/api/screen/home') ? json({ ...HOME, billboard: [HOME.billboard[0], { ...HOME.billboard[0], id: 2, title: 'Dune' }] }) : undefined))
    const feat = await screen.findByRole('region', { name: 'Featured' })
    expect(feat.querySelectorAll('img').length).toBe(2)
    expect(feat.querySelectorAll('.mo-dot').length).toBe(2)
    expect(feat.querySelectorAll('.mo-dot-fill').length).toBe(1)
  })

  it('has For you, Discover and Library under one heading; popular rows lead to Discover, the Library part lists your series and movies, and a search jumps to Discover', async () => {
    const urls = mount()
    expect(await screen.findByRole('region', { name: 'Featured' })).toBeInTheDocument()                    // For you is where it opens
    const row = await screen.findByRole('region', { name: 'Popular movies' })
    fireEvent.click(within(row).getByRole('button', { name: 'See all →' }))
    expect(await screen.findByRole('list', { name: 'Catalogue' })).toBeInTheDocument()                     // "See all" opens Discover
    fireEvent.click(screen.getByRole('tab', { name: 'Library' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Series' }))
    await vi.waitFor(() => expect(urls.some((u) => u.startsWith('/api/screen/series'))).toBe(true))
    fireEvent.click(screen.getByRole('tab', { name: 'For you' }))
    act(() => useSearch.getState().set('dune'))                                                            // typed in the one search box, in the top bar
    expect(await screen.findByRole('tab', { name: 'Discover', selected: true })).toBeInTheDocument()       // a search is always answered by Discover
    await vi.waitFor(() => expect(urls.some((u) => u.includes('search=dune'))).toBe(true))
  })

  it('lets the featured picture run under the top bar while it is on screen, and gives the bar back otherwise', async () => {
    mount()
    await screen.findByRole('region', { name: 'Featured' })
    expect(useUi.getState().immersive).toBe(true)
    await toDiscover()
    await vi.waitFor(() => expect(useUi.getState().immersive).toBe(false))
  })
})
