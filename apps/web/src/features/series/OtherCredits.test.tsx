import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OtherCredits } from './OtherCredits'
import { withRouter } from '@/test/withRouter'

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
afterEach(() => vi.unstubAllGlobals())

describe('OtherCredits', () => {
  it('lists the other movies and shows, each linking to its Discover page', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ok: true, reason: '', items: [{ imdb: 'tmdb-245891', kind: 'movie', title: 'John Wick', year: 2014, poster: '', rating: '7.4', character: 'John Wick' }, { imdb: 'tmdb-77', kind: 'tv', title: 'A Show', year: 2020, poster: '', rating: '' }] })))
    withRouter(<OtherCredits id={501} name="Keanu Reeves" />)
    expect(await screen.findByRole('heading', { name: 'More with Keanu Reeves' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open John Wick' })).toHaveAttribute('href', '/discover/movie/tmdb-245891')
    expect(screen.getByRole('link', { name: 'Open A Show' })).toHaveAttribute('href', '/discover/series/tmdb-77')
    expect(screen.getByText('2014 · John Wick')).toBeInTheDocument()
  })

  it('says what is missing when there is no TMDB key, and shows nothing when there is nothing to say', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ok: true, reason: 'tmdb', items: [] })))
    const first = withRouter(<OtherCredits id={1} name="A" />)
    expect(await screen.findByText(/Add a TMDB key/)).toBeInTheDocument()
    first.unmount?.()
  })
})
