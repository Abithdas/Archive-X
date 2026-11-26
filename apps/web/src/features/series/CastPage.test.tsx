import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CastPage } from './CastPage'
import { withRouter } from '@/test/withRouter'

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
afterEach(() => vi.unstubAllGlobals())

describe('CastPage', () => {
  it('shows the library titles first, then the other movies and shows', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ok: true, name: 'Keanu Reeves', image: '', reason: '', library: [{ id: 4, kind: 'movie', title: 'The Matrix', year: 1999, poster: '' }],
      items: [{ imdb: 'tmdb-245891', kind: 'movie', title: 'John Wick', year: 2014, poster: '', rating: '7.4' }] })))
    withRouter(<CastPage name="Keanu Reeves" />)
    expect(await screen.findByRole('heading', { name: 'In your library' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open The Matrix' })).toHaveAttribute('href', '/movies/4')
    expect(screen.getByRole('heading', { name: 'More with Keanu Reeves' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open John Wick' })).toHaveAttribute('href', '/discover/movie/tmdb-245891')
    const order = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(order).toEqual(['In your library', 'More with Keanu Reeves'])
  })
})
