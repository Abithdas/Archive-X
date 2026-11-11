import { screen, fireEvent, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { readCrop } from './portrait'
import { useSelection } from '@/features/library/selection'
import { PerformerPage } from './PerformerPage'
import { withRouter } from '@/test/withRouter'

const video = { id: 5, source_channel: '-100', title: 'Scene 5', filename: 'f.mp4', size: 1, duration: 60, width: 1920, height: 1080, resolution_category: '1080p', thumbnail_id: 5, favorite: 0, upload_date: null, channel_title: 'Main', performers: ['Mia'], studios: [], tags: [], studio: '' }
const detail = (bio: string) => ({ performer: { id: 2, name: 'Mia Rivera', image: null, bio, profile: 'Born 1995', gender: 'female', favorite: false }, videos: [video] })
afterEach(() => vi.unstubAllGlobals())

describe('PerformerPage shuffle', () => {
  it('starts a shuffled MPC-HC playlist of this performer\'s videos', async () => {
    const posts: Array<{ url: string; body: Record<string, unknown> }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') { posts.push({ url, body: JSON.parse(String(init.body)) }); return new Response('{"ok":true,"player":"MPC-HC","count":1}', { status: 200 }) }
      return new Response(JSON.stringify(detail('bio')), { status: 200 })
    }))
    withRouter(<PerformerPage id={2} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Shuffle in MPC-HC' }))
    await vi.waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]!.url).toBe('/api/play/external')
    expect(posts[0]!.body).toMatchObject({ player: 'mpc', random: true })
    expect((posts[0]!.body.playlist as unknown[]).length).toBe(1)
  })
})

describe('PerformerPage', () => {
  it('shows the profile and her videos, and collapses a long bio', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(detail('x'.repeat(400))), { status: 200 })))
    withRouter(<PerformerPage id={2} />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Mia Rivera' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Scene 5' })).toBeInTheDocument()
    expect(screen.getByText('1 video')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Read more' }))
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
  })
  it('says so when the performer does not exist', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Not found"}', { status: 404 })))
    withRouter(<PerformerPage id={9} />)
    expect(await screen.findByText('Performer not found.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })
})

describe('PerformerPage extras', () => {
  const j = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
  const SUG: Array<Record<string, unknown>> = []
  const mountExtras = (provider = true) => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url === '/api/performers/2/videos') return j({ performer: { id: 2, name: 'Mia Rivera', image: null, bio: '', profile: 'Born 1995', gender: 'female', favorite: false, provider_ids: provider ? '{"stashdb":"x"}' : '{}' }, videos: [{ ...video, studios: ['Northlight'] }] })
      if (url === '/api/performers/2/metadata') return j({ providers: provider ? [{ provider: 'stashdb', normalized: JSON.stringify({ details: { country: 'US', birth_date: '1995-06-15', instagram: 'https://instagram.com/mia', bio: 'Provider bio.' } }) }] : [] })
      if (url === '/api/performers/2/career-counts') return j({ career_counts: [{ provider: 'StashDB', count: 55 }] })
      if (url === '/api/performers/2/suggested') return j(SUG.slice())
      if (url === '/api/performers/2/similar?limit=40') return j({ items: [{ id: 9, name: 'Zoe Ray', video_count: 4 }] })
      if (url === '/api/studios?limit=5000') return j([{ id: 7, name: 'Northlight', video_count: 1 }])
      if (url.startsWith('/api/suggestions/') && init?.method === 'POST') { SUG.length = 0; return j({ ok: true }) }
      return j({ ok: true })
    }))
    withRouter(<PerformerPage id={2} />)
    return calls
  }
  it('shows provider facts, pills, a provider biography and social links', async () => {
    mountExtras()
    expect(await screen.findByText('Provider bio.')).toBeInTheDocument()
    const card = screen.getByRole('region', { name: 'Performer details' })
    expect(within(card).getByText('US')).toBeInTheDocument(); expect(within(card).getByText(/1995-06-15 \(\d+\)/)).toBeInTheDocument(); expect(within(card).getByText('55')).toBeInTheDocument()
    expect(screen.getByText('Metadata linked')).toBeInTheDocument(); expect(screen.getByText('StashDB')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Open Instagram' })).toHaveAttribute('href', 'https://instagram.com/mia')
    expect(screen.getByText('Performer')).toBeInTheDocument()
  })
  it('lists her studios and similar performers on their tabs', async () => {
    mountExtras()
    fireEvent.click(await screen.findByRole('tab', { name: /Studios/ }))
    expect(await screen.findByRole('link', { name: /Northlight/ })).toHaveAttribute('href', '/studios/7')
    fireEvent.click(screen.getByRole('tab', { name: /Similar Performers/ }))
    expect(await screen.findByRole('link', { name: /Zoe Ray/ })).toHaveAttribute('href', '/performers/9')
  })
  it('links a suggested video to this performer', async () => {
    SUG.splice(0, SUG.length, { suggestion_id: 31, channel_id: '-100', message_id: 5, title: 'Maybe Mia' })
    const calls = mountExtras()
    fireEvent.click(await screen.findByRole('tab', { name: /Suggestions/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Yes, link performer/ }))
    await vi.waitFor(() => expect(calls.find((c) => c.url === '/api/suggestions/31/confirm-entity')?.body).toEqual({ entity_type: 'performer', entity_id: 2 }))
    expect(await screen.findByText('No pending suggestions')).toBeInTheDocument()
  })
})

describe('PerformerPage profile card', () => {
  beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  })
  const open = async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(detail('bio')), { status: 200 })))
    withRouter(<PerformerPage id={2} />)
    await screen.findByRole('heading', { level: 1, name: 'Mia Rivera' })
  }
  it('has a back button and a Performers › name trail', async () => {
    await open()
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(nav).getByRole('button', { name: 'Go back' })).toBeInTheDocument()
    expect(nav).toHaveTextContent('Performers › Mia Rivera')
  })
  it('Adjust framing opens the portrait dialog and saves the framing for this performer', async () => {
    localStorage.clear()
    await open()
    expect(screen.getByText('Adjust framing')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Adjust portrait framing for Mia Rivera' }))
    fireEvent.change(await screen.findByRole('slider', { name: 'Portrait zoom' }), { target: { value: '1.8' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save framing' }))
    expect(readCrop(2).zoom).toBe(1.8)
    expect(readCrop(3).zoom).toBe(1)
  })
  it('Edit multiple videos turns on selection with a count, and a second click turns it off', async () => {
    useSelection.getState().exit()
    await open()
    expect(screen.getByText('Videos linked to this performer')).toBeInTheDocument()
    const btn = screen.getByRole('button', { name: 'Edit multiple videos' })
    expect(btn).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(btn)
    expect(screen.getByRole('button', { name: /Selecting · 0/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('toolbar', { name: 'Edit multiple videos' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Selecting · 0/ }))
    expect(screen.queryByRole('toolbar', { name: 'Edit multiple videos' })).not.toBeInTheDocument()
  })
})
