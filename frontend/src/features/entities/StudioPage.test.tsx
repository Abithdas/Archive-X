import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { StudioPage } from './StudioPage'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())

const vid = (id: number, performers: string[]) => ({ id, source_channel: '-100', title: `Scene ${id}`, filename: `f${id}.mp4`, size: 1, duration: 60, width: 1920, height: 1080, resolution_category: '1080p', thumbnail_id: id, favorite: 0, upload_date: null, channel_title: 'Main', performers, studios: [], tags: [], studio: 'Northlight' })
const detail = (site: Record<string, unknown> = {}, videos = [vid(1, ['Mia']), vid(2, ['Mia', 'Zed'])]) => ({ site: { id: 3, name: 'Northlight', image: null, bio: '', provider_ids: {}, favorite: false, ...site }, videos, site_url: 'https://north.example' })
type Call = { url: string; method: string }
const mount = (body: unknown = detail(), code = 200) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET' })
    if (url === '/api/entities/studio/3/favorite') return new Response('{"ok":true,"favorite":true}', { status: 200 })
    if (url.startsWith('/api/performers?')) return new Response('[{"id":8,"name":"Mia","video_count":2}]', { status: 200 })
    if (init?.method === 'POST') return new Response('{"ok":true,"player":"MPC-HC","count":2}', { status: 200 })
    return new Response(JSON.stringify(body), { status: code })
  }))
  withRouter(<StudioPage id={3} />)
  return calls
}

describe('StudioPage', () => {
  it('shows the studio, its counts and its videos', async () => {
    mount()
    expect(await screen.findByRole('heading', { level: 1, name: 'Northlight' })).toBeInTheDocument()
    expect(screen.getAllByText('2 videos').length).toBeGreaterThan(0)
    expect(screen.getByText('2 performers')).toBeInTheDocument()
    expect(screen.getByText('Local profile')).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Scene 1' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Website/ })).toHaveAttribute('href', 'https://north.example')
  })

  it('collapses a long bio and shows provider pills', async () => {
    mount(detail({ bio: 'x'.repeat(400), provider_ids: { stashdb: 'abc' } }))
    expect(await screen.findByText('StashDB')).toBeInTheDocument()
    expect(screen.getByText('Metadata linked')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Read more' }))
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
  })

  it('lists performers on their tab, most videos first, linking known ones', async () => {
    mount()
    fireEvent.click(await screen.findByRole('tab', { name: /Performers/ }))
    const panel = await screen.findByRole('tabpanel', { name: 'Performers' })
    const names = Array.from(panel.querySelectorAll('li')).map((li) => li.textContent)
    expect(names[0]).toContain('Mia')
    expect(names[1]).toContain('Zed')
    await waitFor(() => expect(screen.getByRole('link', { name: /Mia/ })).toHaveAttribute('href', '/performers/8'))
    expect(screen.queryByRole('link', { name: /Zed/ })).not.toBeInTheDocument()
  })

  it('toggles the favourite using the server answer', async () => {
    const calls = mount()
    const header = await screen.findByRole('banner', { name: 'Studio profile' })
    const btn = within(header).getByRole('button', { name: 'Add to favourites' })
    fireEvent.click(btn)
    await waitFor(() => expect(calls.some((c) => c.url === '/api/entities/studio/3/favorite' && c.method === 'POST')).toBe(true))
    await waitFor(() => expect(within(header).getByRole('button', { name: 'Remove from favourites' })).toHaveAttribute('aria-pressed', 'true'))
  })

  it('shuffles this studio\'s videos in MPC-HC', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Shuffle in MPC-HC' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/play/external' && c.method === 'POST')).toBe(true))
  })

  it('opens the editor', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit studio details' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('says so when the studio does not exist, without a retry button', async () => {
    mount({ detail: 'Not found' }, 404)
    expect(await screen.findByText('Studio not found.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })
})
