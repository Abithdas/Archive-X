import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AddonExtras, AddonStreams } from './AddonStreams'

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
    const video = document.querySelector('video')!
    expect(video).toHaveAttribute('src', '/api/screen/stream/tok123')
    expect(screen.getByRole('region', { name: 'Now playing' })).toBeInTheDocument()   // in the page with the app's own player, not in a pop-up
    expect(document.querySelector('dialog[open]')).toBeNull()
  })
  it('asks with the season and episode for a show, and shows nothing when no add-on answers', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); return json({ ok: true, items: [], skipped: 0, addons: [] }) }))
    const { container } = withRouter(<AddonStreams kind="series" ident="tt0903747" season={1} episode={2} />)
    await vi.waitFor(() => expect(urls).toEqual(['/api/screen/streams/series/tt0903747?season=1&episode=2']))
    expect(container.querySelector('[aria-label="From your add-ons"]')).toBeNull()
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
