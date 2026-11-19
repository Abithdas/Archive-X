import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { DiscoverEpisodePage, DiscoverTitlePage, type Title } from './DiscoverPages'

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
const SHOW: Title = {
  imdb: 'tt0903747', kind: 'tv', title: 'Breaking Bad', year: 2008, poster: '', background: 'https://b/bb.jpg', rating: '9.5', overview: 'A teacher turns to crime.', genres: ['Drama'], library: null, runtime: '49 min',
  cast: ['Bryan Cranston'], director: [], trailer: 'abcDEF12345', seasons: 1, logo: '',
  episodes: [{ season: 1, episodes: [{ episode: 1, name: 'Pilot', overview: 'Walt starts.', released: '2008-01-20', thumb: '' }, { episode: 2, name: "Cat's in the Bag...", overview: 'Clean up.', released: '2008-01-27', thumb: '' }] }],
}
const WHERE = { ok: true, region: 'US', link: 'https://www.themoviedb.org/tv/1396/watch?locale=US', groups: [{ key: 'flatrate', label: 'Stream', providers: [{ name: 'Netflix', logo: '', open: 'https://www.netflix.com/search?q=Breaking%20Bad' }, { name: 'Some Service', logo: '' }] }] }
const stub = (title: Title = SHOW) => vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.startsWith('/api/screen/where/') ? json(WHERE) : url.startsWith('/api/screen/discover/') ? json(title) : json({}))))

describe('the page of a title that is not in the library', () => {
  it('shows its story, a button that opens each service, and a page for every episode', async () => {
    stub()
    withRouter(<DiscoverTitlePage kind="series" id="tt0903747" />)
    expect(await screen.findByRole('heading', { name: 'Breaking Bad' })).toBeInTheDocument()
    expect(screen.getByText('A teacher turns to crime.')).toBeInTheDocument()
    expect(screen.getByText('Not in your library')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Trailer/ }))      // the trailer plays in the page with YouTube's own player
    expect(document.querySelector('iframe')).toHaveAttribute('src', expect.stringContaining('https://www.youtube-nocookie.com/embed/abcDEF12345'))
    expect(screen.getByRole('link', { name: /watch it on YouTube/ })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=abcDEF12345')
    const netflix = await screen.findByRole('link', { name: 'Open on Netflix' })
    expect(netflix).toHaveAttribute('href', 'https://www.netflix.com/search?q=Breaking%20Bad'); expect(netflix).toHaveAttribute('target', '_blank')
    expect(screen.getByText('Some Service')).toBeInTheDocument()                                  // a service with no address is named, not linked
    const list = screen.getByRole('list', { name: 'Episodes' })
    expect(within(list).getAllByRole('link')).toHaveLength(2)
    expect(within(list).getByText('1. Pilot')).toBeInTheDocument(); expect(within(list).getByText("2. Cat's in the Bag...")).toBeInTheDocument(); expect(within(list).getByText('Jan 27, 2008')).toBeInTheDocument()
  })
  it('says the show is already in the library when it is, and does not send you elsewhere', async () => {
    stub({ ...SHOW, library: { kind: 'tv', id: 7 } })
    withRouter(<DiscoverTitlePage kind="series" id="tt0903747" />)
    expect(await screen.findByRole('link', { name: /Open in your library/ })).toBeInTheDocument()
    expect(screen.getByText(/nothing to look for elsewhere/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open on Netflix' })).toBeNull()
  })
  it('has a page for an episode with its story, the ones around it and where the show is on', async () => {
    stub()
    withRouter(<DiscoverEpisodePage id="tt0903747" season={1} episode={1} />)
    expect(await screen.findByRole('heading', { name: 'Pilot' })).toBeInTheDocument()
    expect(screen.getByText('Walt starts.')).toBeInTheDocument(); expect(screen.getByText(/Season 1 · Episode 1/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'S01E02 →' })).toBeInTheDocument(); expect(screen.queryByRole('link', { name: /←/ })).toBeNull()
    expect(await screen.findByRole('link', { name: 'Open on Netflix' })).toBeInTheDocument()
  })
  it('says so when an episode is not in the catalogue', async () => {
    stub()
    withRouter(<DiscoverEpisodePage id="tt0903747" season={9} episode={9} />)
    expect(await screen.findByText(/not in the catalogue/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: /Back to Breaking Bad/ }))
  })
})
