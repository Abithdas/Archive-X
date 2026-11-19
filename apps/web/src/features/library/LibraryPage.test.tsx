import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { useLibraryStore } from './store'
import { LibraryPage } from './LibraryPage'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), loading: vi.fn(), dismiss: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())
beforeEach(() => { try { localStorage.clear() } catch { /* no storage */ } useLibraryStore.setState({ channels: [], page: 0 }) })

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
const CHANNELS = [
  { id: 1, telegram_id: '-1001', title: 'Main', username: null, image: null, role: null, video_count: 1, known_count: 1 },
  { id: 2, telegram_id: '-1002', title: 'Second', username: null, image: null, role: null, video_count: 1, known_count: 1 },
]
const item = { id: 70, _cid: '-1001', media_id: 7, name: 'First scene', filename: 'f.mp4', size: 1, duration: 60, width: 1920, height: 1080, resolution_category: '1080p', thumbnail_id: 0, favorite: 0, upload_date: '2026-01-01', channel_title: 'Main', performers: [], studios: [], tags: [], studio: '' }

describe('LibraryPage', () => {
  it('shows the videos of every channel on a fresh start, without picking a channel first', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url)
      if (url === '/api/channels') return json(CHANNELS)
      if (url.startsWith('/api/library?')) return json({ total: 1, page: 0, per: 24, items: [item] })
      return json({})
    }))
    withRouter(<LibraryPage />)
    expect(await screen.findByRole('article', { name: 'First scene' })).toBeInTheDocument()
    const asked = urls.find((u) => u.startsWith('/api/library?')) ?? ''
    expect(new URLSearchParams(asked.split('?')[1]).get('channels')).toBe('-1001,-1002')
    await waitFor(() => expect(screen.getByText(/videos? · 2 channels/)).toBeInTheDocument())   // the count itself is a labelled counter
    expect(screen.getByRole('img', { name: '1' })).toBeInTheDocument()
  })

  it('stays on the page it was on when it opens again (page 10 does not fall back to page 1 while the list loads)', async () => {
    useLibraryStore.setState({ channels: [], page: 9 })
    const asked: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/channels') return json(CHANNELS)
      if (url.startsWith('/api/library?')) { asked.push(url); await new Promise((r) => setTimeout(r, 150)); return json({ total: 900, page: 9, per: 24, items: [item] }) }
      return json({})
    }))
    withRouter(<LibraryPage />)
    expect(await screen.findByRole('article', { name: 'First scene' })).toBeInTheDocument()
    expect(useLibraryStore.getState().page).toBe(9)
    expect(asked.every((u) => new URLSearchParams(u.split('?')[1]).get('page') === '9')).toBe(true)
  })

  function stack(mode: 'sfw' | 'nsfw') {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/content-mode') return json({ ok: true, mode, pin_set: true, locked_for: 0 })
      if (url === '/api/channels') return json(CHANNELS)
      if (url.startsWith('/api/library?')) return json({ total: 1, page: 0, per: 24, items: [item] })
      if (url.startsWith('/api/screen/movies')) return json({ items: [{ id: 1, title: 'The Matrix', year: 1999, poster: '', cam_only: false, copies: 1, best: '1080p' }] })
      if (url.startsWith('/api/screen/series')) return json({ items: [{ id: 2, title: 'Dark', year: 2017, poster: '', overview: '', genres: [], episodes: 2, seasons: 1 }] })
      if (url.startsWith('/api/screen/home')) return json({ continue: [], airing: [], recent: [], billboard: [] })
      if (url.startsWith('/api/screen/discover')) return json({ ok: true, items: [], more: false })
      return json({})
    }))
  }
  it('in SFW the videos page is Home: movies and shows as cards, and Discover, never one card for each episode', async () => {
    stack('sfw')
    withRouter(<LibraryPage />)
    expect(await screen.findByRole('region', { name: 'Discover' })).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'First scene' })).toBeNull()
    expect(screen.queryByRole('tab', { name: 'All' })).toBeNull()                      // the old All | Movies | Series switch is gone
    fireEvent.click(screen.getByRole('button', { name: 'All series' }))
    expect(await screen.findByRole('link', { name: 'Open Dark' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'All movies' }))
    expect(await screen.findByRole('link', { name: 'Open The Matrix' })).toBeInTheDocument()
  })
  it('in NSFW has no such switch', async () => {
    stack('nsfw')
    withRouter(<LibraryPage />)
    expect(await screen.findByRole('article', { name: 'First scene' })).toBeInTheDocument()
    expect(screen.queryByRole('tab')).toBeNull()
  })
})
