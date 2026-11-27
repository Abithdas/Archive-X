import { useSearch } from '@/app/search'
import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { labelsFor } from '@/features/content/labels'
import { SeriesDetailPage, SeriesList } from './SeriesPage'
import { episodeLabel, lengthText, seasonSummary } from './series'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })

describe('helpers', () => {
  it('labels episodes, lengths and seasons', () => {
    expect(episodeLabel(2, { episode: 3 })).toBe('S02E03')
    expect(episodeLabel(0, { episode: 4 })).toBe('Special 4')
    expect(episodeLabel(1, { episode: null })).toBe('')
    expect(lengthText({ duration: 3000, runtime: 40 })).toBe('50 min')
    expect(lengthText({ duration: 0, runtime: 42 })).toBe('42 min')
    expect(seasonSummary({ season: 1, name: 'Season 1', total: 8, episodes: [{} as never, {} as never] })).toBe('2 of 8 episodes here')
    expect(seasonSummary({ season: 1, name: 'Season 1', total: 0, episodes: [{} as never] })).toBe('1 episode')
  })
  it('words change with the mode, and stay familiar until it is known', () => {
    expect(labelsFor('sfw')).toMatchObject({ videos: 'Home', performers: 'Cast', studios: 'Studios & Networks', tags: 'Genres', series: 'Series' })
    expect(labelsFor('nsfw')).toMatchObject({ videos: 'Videos', performers: 'Performers', studios: 'Studios', tags: 'Tags', series: null })
    expect(labelsFor(undefined).performers).toBe('Performers')
  })
})

describe('Series pages', () => {
  it('lists shows with their episode counts', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [{ id: 1, title: 'Dark', year: 2017, poster: '', overview: '', genres: [], episodes: 2, seasons: 2 }] })))
    withRouter(<SeriesList />)
    expect(await screen.findByRole('link', { name: 'Open Dark' })).toBeInTheDocument()
    expect(screen.getByText('2017 · 2 episodes')).toBeInTheDocument()
  })
  it('says what to do when there are no shows', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [] })))
    withRouter(<SeriesList />)
    expect(await screen.findByText('No shows yet')).toBeInTheDocument()
  })
  it('shows a show with its seasons and episodes', async () => {
    const d = { id: 1, title: 'Dark', year: 2017, overview: 'A missing child.', tagline: '', poster: '', backdrop: '', genres: ['Drama'], companies: ['Netflix'], cast: [{ name: 'Louis Hofmann', character: 'Jonas', profile: '' }], rating: 8, status: '',
      seasons: [{ season: 2, name: 'Season 2', total: 8, episodes: [{ episode: 3, name: 'Dark Matter', overview: 'Ep.', air_date: '2019-06-21', still: '', runtime: 55, duration: 3300, cid: '-9000000000001', mid: 1 }] }] }
    vi.stubGlobal('fetch', vi.fn(async () => json(d)))
    withRouter(<SeriesDetailPage id={1} />)
    expect(await screen.findByText(/Dark Matter/)).toBeInTheDocument()
    expect(screen.getAllByText(/S02E03/).length).toBeGreaterThan(0)
    expect(screen.getByText('1 of 8 episodes here')).toBeInTheDocument()
    expect(screen.getByText(/Louis Hofmann/)).toBeInTheDocument()
  })
})

describe('missing episodes and movies', () => {
  it('writes missing episodes as runs', async () => {
    const { missingText } = await import('./series')
    expect(missingText([1, 2, 5, 6, 7, 9])).toBe('E01–E02, E05–E07, E09')
    expect(missingText([])).toBe('')
  })
  it('sorts and finds movies, and words their copies', async () => {
    const { sortMovies, movieBadge, versionText, sizeText } = await import('./series')
    const m = (id: number, title: string, year: number, rating: number, added: number) => ({ id, title, year, poster: '', overview: '', genres: [], rating, release_date: '', added, versions: 1, best: '1080p', cam_only: false })
    const list = [m(1, 'Heat', 1995, 8.3, 5), m(2, 'Alien', 1979, 8.5, 9), m(3, 'Dune', 2021, 8, 1)]
    expect(sortMovies(list, 'title', '').map((x) => x.title)).toEqual(['Alien', 'Dune', 'Heat'])
    expect(sortMovies(list, 'recent', '').map((x) => x.title)).toEqual(['Alien', 'Heat', 'Dune'])
    expect(sortMovies(list, 'year', '').map((x) => x.title)).toEqual(['Dune', 'Heat', 'Alien'])
    expect(sortMovies(list, 'rating', '').map((x) => x.title)).toEqual(['Alien', 'Heat', 'Dune'])
    expect(sortMovies(list, 'title', ' he').map((x) => x.title)).toEqual(['Heat'])
    expect(movieBadge({ best: '1080p', versions: 2, cam_only: false })).toBe('1080p · 2 copies')
    expect(movieBadge({ best: '480p', versions: 1, cam_only: true })).toBe('Cinema recording')
    expect(versionText({ resolution: '1080p', source: 'WEB-DL', codec: 'h264', size: 2.5 * 1024 ** 3, cam: false })).toBe('1080p · WEB-DL · H264 · 2.5 GB')
    expect(sizeText(300 * 1024 ** 2)).toBe('300 MB')
  })
  it('shows a movie page with its copies, best first', async () => {
    const d = { id: 1, title: 'The Matrix', year: 1999, overview: 'A hacker.', tagline: '', poster: '', backdrop: '', genres: ['Action'], companies: [], rating: 8, runtime: 136, cast: [{ name: 'Keanu Reeves', character: 'Neo', profile: '' }],
      versions: [{ cid: '-9000000000001', mid: 2, media_id: 2, filename: 'The.Matrix.1080p.mkv', resolution: '1080p', height: 1080, source: 'WEB-DL', cam: false, codec: 'h264', size: 2 * 1024 ** 3, duration: 8100 }, { cid: '-9000000000001', mid: 1, media_id: 1, filename: 'The.Matrix.720p.mkv', resolution: '720p', height: 720, source: '', cam: false, codec: '', size: 0, duration: 8100 }] }
    vi.stubGlobal('fetch', vi.fn(async () => json(d)))
    const { MovieDetailPage } = await import('./MoviesPage')
    withRouter(<MovieDetailPage id={1} />)
    expect(await screen.findByText('Choose a copy (2)')).toBeInTheDocument()
    expect(screen.getByText('Best')).toBeInTheDocument()
    expect(screen.getByText('1080p · WEB-DL · H264 · 2.0 GB')).toBeInTheDocument()
  })
  it('lists movies as one poster each', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [{ id: 1, title: 'The Matrix', year: 1999, poster: '', overview: '', genres: [], rating: 8, release_date: '', added: 3, versions: 2, best: '1080p', cam_only: false }] })))
    const { MoviesList } = await import('./MoviesPage')
    withRouter(<MoviesList />)
    expect(await screen.findByRole('link', { name: 'Open The Matrix' })).toBeInTheDocument()
    expect(screen.getByText('1080p · 2 copies')).toBeInTheDocument()
  })
  it('says which episodes of a season are missing', async () => {
    const d = { id: 1, title: 'Dark', year: 2017, overview: '', tagline: '', poster: '', backdrop: '', genres: [], companies: [], cast: [], rating: 8, status: '',
      seasons: [{ season: 1, name: 'Season 1', total: 4, missing: [3, 4], episodes: [{ episode: 1, name: 'A', overview: '', air_date: '', still: '', runtime: 0, duration: 0, cid: '-1', mid: 1 }] }, { season: 2, name: 'Season 2', total: 8, missing: [1, 2, 3, 4, 5, 6, 7, 8], episodes: [] }] }
    vi.stubGlobal('fetch', vi.fn(async () => json(d)))
    withRouter(<SeriesDetailPage id={1} />)
    expect(await screen.findByText('Missing: E03–E04')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Season 2' }))
    expect(screen.getByText('None of this season is here (8 episodes)')).toBeInTheDocument()
  })
})

describe('episodes the library does not have', () => {
  it('are listed with the catalogue names and open the episode page where the add-ons offer streams; the cast opens that person', async () => {
    const d = { id: 1, imdb: 'tt5753856', title: 'Dark', year: 2017, overview: '', tagline: '', poster: '', backdrop: '', genres: [], companies: [], cast: [{ name: 'Louis Hofmann', character: 'Jonas', profile: '' }], rating: 8, status: '',
      seasons: [{ season: 3, name: 'Season 3', total: 3, missing: [1, 2], episodes: [{ episode: 3, name: 'Here', overview: '', air_date: '', still: '', runtime: 0, duration: 0, cid: '-1', mid: 3 }] }] }
    const found = { imdb: 'tt5753856', kind: 'tv', title: 'Dark', year: 2017, poster: '', background: '', rating: '', overview: '', genres: [], library: null, runtime: '', cast: [], director: [], trailer: '', seasons: 3, episodes: [{ season: 3, episodes: [{ episode: 1, name: 'Deja-vu', overview: '', released: '2020-06-27', thumb: '' }, { episode: 2, name: 'The Ouroboros', overview: '', released: '', thumb: '' }] }] }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => json(String(url).includes('/api/screen/discover/') ? found : d)))
    withRouter(<SeriesDetailPage id={1} />)
    const gap = await screen.findByRole('link', { name: 'Episode 1 is not in your library: find it in your add-ons' })
    expect(gap).toHaveAttribute('href', '/discover/series/tt5753856/3/1')
    expect(await screen.findByText(/Deja-vu/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Everything with Louis Hofmann' })).toHaveAttribute('href', '/cast/Louis%20Hofmann')
  })
})

describe('Filters on the Movies and Series lists', () => {
  const uhd = { res: [2160], sources: ['BluRay'], hdr: ['HDR'], langs: ['German'] }
  const web = { res: [720], sources: ['WEB-DL'], hdr: [], langs: [] }
  it('narrow the movies by quality, source, HDR and language, and say how many are shown', async () => {
    const movie = (id: number, title: string, f: object) => ({ id, title, year: 2020, poster: '', overview: '', genres: [], rating: 7, release_date: '', added: id, versions: 1, best: '', cam_only: false, ...f })
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [movie(1, 'Dune', uhd), movie(2, 'Heat', web)] })))
    const { MoviesList } = await import('./MoviesPage')
    withRouter(<MoviesList />)
    expect(await screen.findByText('Dune')).toBeInTheDocument()
    expect(screen.getByText('· 2 movies')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    for (const g of ['Quality', 'Source', 'HDR / DV', 'Language']) expect(screen.getByRole('group', { name: g })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '4K' }))
    expect(screen.queryByText('Heat')).toBeNull(); expect(screen.getByText('Dune')).toBeInTheDocument()
    expect(screen.getByText('· 1 of 2 movies')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByText('Heat')).toBeInTheDocument()
  })
  it('narrow the shows the same way', async () => {
    const show = (id: number, title: string, f: object) => ({ id, title, year: 2017, poster: '', overview: '', genres: [], episodes: 2, seasons: 1, ...f })
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [show(1, 'Dark', uhd), show(2, 'Lost', web)] })))
    withRouter(<SeriesList />)
    expect(await screen.findByText('Dark')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    fireEvent.click(screen.getByRole('button', { name: 'WEB-DL' }))
    expect(screen.queryByText('Dark')).toBeNull(); expect(screen.getByText('Lost')).toBeInTheDocument()
    expect(screen.queryByLabelText('Find a show')).toBeNull()                    // the top bar's search is the only search box
    act(() => useSearch.getState().set('zzz'))
    expect(await screen.findByText('No show matches')).toBeInTheDocument()
    act(() => useSearch.getState().set(''))
  })
})

describe('The copies of one movie', () => {
  const base = { cid: '-1', media_id: 1, cam: false, codec: 'hevc', duration: 6000 }
  const detail = { id: 1, title: 'Dune', year: 2021, overview: '', tagline: '', poster: '', backdrop: '', genres: [], companies: [], rating: 8, runtime: 155, cast: [], versions: [
    { ...base, mid: 1, filename: 'Dune.2160p.BluRay.HDR.DV.German.mkv', resolution: '2160p', height: 2160, source: 'BluRay', size: 30e9, hdr: ['HDR', 'DV'], languages: ['German'] },
    { ...base, mid: 2, filename: 'Dune.1080p.WEB-DL.mkv', resolution: '1080p', height: 1080, source: 'WEB-DL', size: 8e9, hdr: [], languages: [] },
    { ...base, mid: 3, filename: 'Dune.720p.WEB-DL.mkv', resolution: '720p', height: 720, source: 'WEB-DL', size: 3e9, hdr: [], languages: [] },
  ] }
  it('show a 4K / Dolby Vision badge and can be filtered and sorted like a stream list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(detail)))
    const { MovieDetailPage } = await import('./MoviesPage')
    withRouter(<MovieDetailPage id={1} />)
    expect(await screen.findByText('Choose a copy (3)')).toBeInTheDocument()
    expect(screen.getByText('Dolby Vision')).toBeInTheDocument(); expect(screen.getByText('4K')).toBeInTheDocument()
    expect(screen.getByText('3 copies')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    fireEvent.click(screen.getByRole('button', { name: 'WEB-DL' }))
    expect(screen.getByText('2 of 3 copies')).toBeInTheDocument()
    expect(screen.queryByText('Dune.2160p.BluRay.HDR.DV.German.mkv')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Smallest file' }))
    const names = screen.getAllByText(/^Dune\.\d+p\.WEB-DL\.mkv$/).map((e) => e.textContent)
    expect(names).toEqual(['Dune.720p.WEB-DL.mkv', 'Dune.1080p.WEB-DL.mkv'])
  })
  it('say so when no copy matches, with a way back', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(detail)))
    const { MovieDetailPage } = await import('./MoviesPage')
    withRouter(<MovieDetailPage id={1} />)
    await screen.findByText('Choose a copy (3)')
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Dolby Vision' }))
    fireEvent.click(screen.getByRole('button', { name: 'WEB-DL' }))
    expect(await screen.findByText(/No copy matches these filters/)).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]!)
    expect(await screen.findAllByText(/^Dune\./)).toHaveLength(3)
  })
})
