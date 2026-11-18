import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { labelsFor } from '@/features/content/labels'
import { SeriesDetailPage, SeriesPage } from './SeriesPage'
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
    expect(labelsFor('sfw')).toMatchObject({ videos: 'Movies & Shows', performers: 'Cast', studios: 'Studios & Networks', tags: 'Genres', series: 'Series' })
    expect(labelsFor('nsfw')).toMatchObject({ videos: 'Videos', performers: 'Performers', studios: 'Studios', tags: 'Tags', series: null })
    expect(labelsFor(undefined).performers).toBe('Performers')
  })
})

describe('Series pages', () => {
  it('lists shows with their episode counts', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [{ id: 1, title: 'Dark', year: 2017, poster: '', overview: '', genres: [], episodes: 2, seasons: 2 }] })))
    withRouter(<SeriesPage />)
    expect(await screen.findByRole('link', { name: 'Open Dark' })).toBeInTheDocument()
    expect(screen.getByText('2017 · 2 episodes')).toBeInTheDocument()
  })
  it('says what to do when there are no shows', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ items: [] })))
    withRouter(<SeriesPage />)
    expect(await screen.findByText('No shows yet')).toBeInTheDocument()
  })
  it('shows a show with its seasons and episodes', async () => {
    const d = { id: 1, title: 'Dark', year: 2017, overview: 'A missing child.', tagline: '', poster: '', backdrop: '', genres: ['Drama'], companies: ['Netflix'], cast: [{ name: 'Louis Hofmann', character: 'Jonas', profile: '' }], rating: 8, status: '',
      seasons: [{ season: 2, name: 'Season 2', total: 8, episodes: [{ episode: 3, name: 'Dark Matter', overview: 'Ep.', air_date: '2019-06-21', still: '', runtime: 55, duration: 3300, cid: '-9000000000001', mid: 1 }] }] }
    vi.stubGlobal('fetch', vi.fn(async () => json(d)))
    withRouter(<SeriesDetailPage id={1} />)
    expect(await screen.findByText('Dark Matter')).toBeInTheDocument()
    expect(screen.getByText('S02E03')).toBeInTheDocument()
    expect(screen.getByText('1 of 8 episodes here')).toBeInTheDocument()
    expect(screen.getByText(/Louis Hofmann/)).toBeInTheDocument()
  })
})
